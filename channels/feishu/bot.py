"""飞书 WebSocket 长连接 + 消息处理入口。

对外接口：start() / stop() / is_connected()
SDK 内置 auto_reconnect，start() 跑在 daemon 线程（同步阻塞），进程退出自动终止。

收到消息后：去重 → 时效检查（>5min 拒绝）→ 限流 → 取消该用户旧任务 → 异步推理。
用户连发消息时新消息取消旧任务，旧任务静默退出不回复。

Event loop 绑定问题（已修复）:
    lark_oapi/ws/client.py 顶层执行 `loop = asyncio.get_event_loop()`，
    首次 import 时绑定到所在线程。若主线程先 import，daemon 线程调 start() 会报
    "This event loop is already running"，_connect() 协程从未 await。
    修复：reply.py 顶层不 import lark_oapi；_run_ws() import 后 monkey-patch
    lark_oapi.ws.client.loop 为 daemon 线程的 loop。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime

# 注意：不要在此处 import 任何 lark_oapi 模块！
# 所有 lark 导入已移入 _run_ws()，在该函数内先建 loop 再 import。
# 原因见模块文档字符串 "Event loop 绑定问题"。

from channels.feishu.reply import send_reply
from config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_ENABLED
from core.chat import run_chat
from core.memory import MemoryManager
from core.rate_limit import RateLimiter
from core.trace import TraceLogger

logger = logging.getLogger(__name__)

# ── 外部注入（main.py lifespan 中初始化）──────────────────────
memory_manager: MemoryManager | None = None
trace_logger: TraceLogger | None = None
rate_limiter: RateLimiter | None = None

# ── 重连策略 ─────────────────────────────────────────────────
_AGENT_TIMEOUT = 90                     # Agent 推理超时（秒）
_STALE_MSG_THRESHOLD = 300              # 消息超过 5 分钟视为延迟到达（秒）

# ── 内部状态 ─────────────────────────────────────────────────
_ws_client = None  # type: ignore — 实际类型在 _run_ws 中延迟导入
_ws_loop: asyncio.AbstractEventLoop | None = None  # daemon 线程的事件循环，stop() 跨线程调度用
_ws_thread: threading.Thread | None = None
_connected = False
_stop_requested = False  # stop() 置位后，_run_ws() 据此区分正常停止与异常退出
# 连接状态：disabled | no_credentials | connecting | connected | disconnected
_connection_status = "disabled"
# 每个 open_id 当前活跃的推理任务；新消息到达时取消旧任务，确保始终回答最新问题
_active_tasks: dict[str, asyncio.Task] = {}


# ── 消息解析 ──────────────────────────────────────────────────


def _parse_message_text(content: str) -> str:
    """解析飞书消息内容，提取纯文本。

    飞书消息 content 是 JSON 字符串，格式为:
      {"text": "用户发送的文本"}
    """
    if not content:
        return ""
    try:
        data = json.loads(content)
        return data.get("text", "")
    except json.JSONDecodeError:
        return ""


# ── 消息处理 ──────────────────────────────────────────────────

async def _process_message(open_id: str, text: str, msg_id: str, create_time_ms: str = "") -> None:
    """处理单条飞书消息：去重 → 时效 → 限流 → 取消旧任务 → 启动推理。"""
    logger.info("飞书消息 open_id=%s msg_id=%s text=%s", open_id, msg_id, text[:100])

    # 1. 去重：同一 msg_id 只处理一次
    DEDUP_TTL = 3600
    if rate_limiter and rate_limiter._redis:
        added = await rate_limiter._redis.set(
            f"lg:feishu:dedup:{msg_id}", "1", ex=DEDUP_TTL, nx=True,
        )
        if added == None:
            logger.info("飞书消息重复投递，跳过 msg_id=%s", msg_id)
            return

    # 2. 消息为空
    if not text:
        await send_reply("请发送有效的问题", msg_id)
        return

    # 3. 时效检查：过旧消息（断线重连补投）或时间戳解析异常 → 回复用户并跳过
    if create_time_ms:
        try:
            create_time = datetime.fromtimestamp(int(create_time_ms) / 1000)
            age = (datetime.now() - create_time).total_seconds()
            if age > _STALE_MSG_THRESHOLD:
                logger.warning("飞书消息延迟 %.0f 秒，跳过推理 open_id=%s", age, open_id)
                await send_reply("您的消息因网络原因延迟到达，如仍需回答请重新发送", msg_id)
                return
        except (ValueError, TypeError):
            logger.warning("飞书 create_time 解析失败，视为过期 msg_id=%s", msg_id)
            await send_reply("消息时间戳异常，请重新发送", msg_id)
            return

    # 4. 限流检查
    if rate_limiter:
        allowed, retry_after = await rate_limiter.check_by_user_id(open_id)
        if not allowed:
            await send_reply(f"请求太频繁，请 {retry_after} 秒后再试", msg_id)
            logger.info("飞书限流命中 open_id=%s retry_after=%d", open_id, retry_after)
            return

    # 5. 取消该用户上一个推理任务（同步块，无 await，防止竞态）
    old_task = _active_tasks.get(open_id)
    if old_task != None and not old_task.done():
        old_task.cancel()
        logger.info("飞书取消旧任务 open_id=%s", open_id)

    # 6. 启动新推理任务（占位回复在 _run_agent 内发送）
    task = asyncio.create_task(_run_agent(open_id, text, msg_id))
    _active_tasks[open_id] = task


async def _run_agent(open_id: str, text: str, msg_id: str) -> None:
    """占位回复 → 推理 → 最终回复。被取消静默退出；超时/异常仅活跃任务才回复。"""
    current_task = asyncio.current_task()
    try:
        # 1. 占位回复
        await send_reply("正在查询中…", msg_id)

        # 2. Agent 推理
        result = await asyncio.wait_for(
            _do_run_chat(open_id, text),
            timeout=_AGENT_TIMEOUT,
        )

        # 3. 最终回复（仅当仍是活跃任务）
        if _active_tasks.get(open_id) == current_task:
            await send_reply(result, msg_id)
    except asyncio.CancelledError:
        # 被新消息取消，静默退出
        return
    except asyncio.TimeoutError:
        logger.warning("飞书 Agent 超时 open_id=%s", open_id)
        if _active_tasks.get(open_id) == current_task:
            await send_reply("处理超时，请简化问题重试", msg_id)
    except Exception:
        logger.exception("飞书 Agent 异常 open_id=%s", open_id)
        if _active_tasks.get(open_id) == current_task:
            await send_reply("处理出错，请稍后重试", msg_id)
    finally:
        if _active_tasks.get(open_id) == current_task:
            _active_tasks.pop(open_id, None)


async def _do_run_chat(open_id: str, text: str) -> str:
    """实际调用 run_chat 管道，返回完整 answer 文本。"""
    if not memory_manager or not trace_logger:
        return "服务暂时不可用"

    result = await run_chat(
        session_id=open_id,
        user_message=text,
        memory=memory_manager,
        trace=trace_logger,
    )
    return result["answer"]


# ── 生命周期 ──────────────────────────────────────────────────

async def start(
    mem: MemoryManager,
    trace: TraceLogger,
    rl: RateLimiter,
) -> None:
    """注入依赖并启动 WebSocket 后台线程。FEISHU_ENABLED=False 时跳过。"""
    global memory_manager, trace_logger, rate_limiter, _connection_status
    memory_manager = mem
    trace_logger = trace
    rate_limiter = rl

    if not FEISHU_ENABLED:
        _connection_status = "disabled"
        logger.info("飞书已禁用（FEISHU_ENABLED=false），跳过 WebSocket 连接")
        return

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        _connection_status = "no_credentials"
        logger.warning("飞书已启用但缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET，跳过连接")
        return

    _connection_status = "connecting"
    logger.info("飞书 WebSocket 长连接启动中…")
    _start_ws_thread()


async def stop() -> None:
    """跨线程关闭：run_coroutine_threadsafe 调 _disconnect()，再 stop loop。"""
    global _ws_client, _ws_loop, _connected, _stop_requested, _connection_status
    _connected = False
    _connection_status = "disconnected"
    _stop_requested = True
    if _ws_client != None and _ws_loop != None:
        logger.info("飞书 WebSocket 正在关闭…")
        # 1. 断开 WebSocket 连接
        try:
            future = asyncio.run_coroutine_threadsafe(
                _ws_client._disconnect(), _ws_loop,
            )
            future.result(timeout=5)
        except Exception:
            logger.exception("飞书 WebSocket 断开连接异常")
        # 2. 停止 event loop，让 start() 中的 run_until_complete 退出
        try:
            _ws_loop.call_soon_threadsafe(_ws_loop.stop)
        except Exception:
            logger.exception("飞书 WebSocket 停止 loop 异常")
        _ws_client = None
        _ws_loop = None



def get_connection_status() -> dict:
    """返回 {"status": connected|connecting|disabled|no_credentials|disconnected}。"""
    return {"status": _connection_status}


# ── WebSocket 线程管理 ─────────────────────────────────────────

async def _connection_watchdog():
    """每 30s 检查 _connected，连续 3 次 False 告警。"""
    fail_count = 0
    while not _stop_requested:
        await asyncio.sleep(30)
        if not _connected:
            fail_count += 1
            if fail_count >= 3:
                logger.error("飞书 WebSocket 断线超过 %d 秒！", fail_count * 30)
        else:
            fail_count = 0


def _run_ws() -> None:
    """daemon 线程入口：建 loop → 重建 Redis 连接 → import SDK → monkey-patch loop → start()。"""
    global _ws_client, _ws_loop, _connected, _stop_requested, _connection_status
    global memory_manager, trace_logger, rate_limiter

    # 1. 为 daemon 线程创建专属事件循环
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _ws_loop = _loop

    # 2. daemon 线程重建 Redis 连接：主线程的 redis.asyncio 连接池绑主线程 loop，
    #    跨 loop 用会静默失败（except 把 _redis 设 None 走内存 fallback）。
    memory_manager = MemoryManager()
    trace_logger = TraceLogger()
    rate_limiter = RateLimiter()
    _loop.run_until_complete(memory_manager.connect())
    _loop.run_until_complete(trace_logger.connect())
    _loop.run_until_complete(rate_limiter.connect())
    logger.info("飞书 daemon 线程 Redis 连接已建立")

    try:
        # 3. 延迟导入 SDK 模块（在 try 内，导入失败也能记录日志）
        import lark_oapi as lark
    except Exception:
        logger.exception("飞书 lark_oapi 导入失败，请检查 lark-oapi 是否正确安装")
        _connection_status = "disconnected"
        _ws_loop = None
        return

    # 4. 强制将 SDK 模块级 loop 替换为当前线程 loop（防止主线程预导入导致绑定错误）
    lark.ws.client.loop = _loop

    # 5. 构建事件处理器
    def do_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        """处理接收消息事件

        当用户向机器人发送私聊消息时触发。提取消息内容和发送者信息，
        调度异步 Agent 处理。
        """
        try:
            event = data.event
            message = event.message
            msg_type = message.message_type

            # 只处理文本消息（post 是机器人自己发的富文本，跳过防止循环）
            if msg_type != "text":
                return

            msg_id = message.message_id or ""
            text = _parse_message_text(message.content)
            if not text:
                return

            # 获取发送者 open_id（优先 open_id，兜底 user_id）
            sender_id = event.sender.sender_id
            open_id = sender_id.open_id or ""
            if not open_id:
                open_id = sender_id.user_id or ""

            if not open_id:
                return

            # 消息创建时间（毫秒时间戳），用于延迟消息检测
            create_time_ms = getattr(message, "create_time", "") or ""

            _loop.create_task(_process_message(open_id, text.strip(), msg_id, create_time_ms))
        except Exception as e:
            logger.error("飞书消息处理异常: %s", e)

    _event_handler = (
        lark.EventDispatcherHandler.builder(FEISHU_APP_ID, FEISHU_APP_SECRET)
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .build()
    )

    # 6. 创建客户端并启动（同步阻塞）
    _ws_client = lark.ws.Client(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        event_handler=_event_handler,
        auto_reconnect=True,
    )

    try:
        logger.info("飞书 WebSocket 线程启动")
        _connected = True
        _connection_status = "connected"
        _stop_requested = False
        _loop.create_task(_connection_watchdog())  # 启动断线监控
        _ws_client.start()
    except Exception:
        if _stop_requested:
            # stop() 主动停止 loop 会导致 run_until_complete 抛 RuntimeError，
            # 属于正常关闭流程，不记录异常堆栈
            logger.info("飞书 WebSocket 已正常停止")
        else:
            logger.exception("飞书 WebSocket 线程异常退出")
    finally:
        _connected = False
        _connection_status = "disconnected"
        _ws_loop = None


def _start_ws_thread() -> None:
    """启动飞书 WebSocket 后台线程（daemon，进程退出时自动终止）。"""
    global _ws_thread

    if _ws_thread != None and _ws_thread.is_alive():
        logger.warning("飞书 WebSocket 线程已在运行，跳过重复启动")
        return

    _ws_thread = threading.Thread(target=_run_ws, daemon=True, name="feishu-ws")
    _ws_thread.start()
