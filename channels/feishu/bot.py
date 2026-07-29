"""飞书 WebSocket 长连接管理 + 消息处理入口。

对外接口:
    start()           — 初始化 lark SDK client，启动长连接后台任务
    stop()            — 优雅关闭长连接
    is_connected()    — 飞书连接状态查询

内部流程（收到消息后）:
    1. 提取 open_id, msg_id, 文本内容, create_time
    2. 时效检查: create_time 距今 > 5 分钟 → 回复"消息延迟到达" → 结束
       （WebSocket 断线重连后飞书服务端会补投积压消息，此时已无意义）
    3. 限流: check_by_user_id(open_id)
       |- 超限 → 回复"请求太频繁" → 结束
       |- 放行 → 继续
    4. 取消该用户上一个推理任务（如有），确保始终回答最新问题
    5. 异步 agent（_run_agent）:
       session_id = open_id
       asyncio.create_task(_run_agent(...))
         |
         v (后台执行)
       占位回复 → run_chat 管道 → 路由 → Agent → answer
         |
         v
       回复结果: send_reply(answer, msg_id)

    用户连发消息时，新消息自动取消旧任务，旧任务静默退出不回复。

断连重连:
    lark-oapi SDK 内置 auto_reconnect，无需本模块自行管理。
    启动时在独立线程中运行 ws_client.start()（同步阻塞），
    进程退出时 daemon 线程自动终止。

Event loop 绑定问题（已修复）:
    lark_oapi/__init__.py 顶层 `from . import ws` 会导致
    lark_oapi/ws/client.py 在首次导入时执行模块级
    `loop = asyncio.get_event_loop()`，将 loop 绑定到导入时所在线程。
    如果绑定到主线程（uvicorn），daemon 线程中 start() 会因
    "This event loop is already running" 失败，_connect() 协程未被 await，
    触发 "coroutine 'Client._connect' was never awaited"。

    修复方案（双保险）:
    1. reply.py 延迟导入 lark_oapi，避免主线程触发包加载
    2. _run_ws() 导入后 monkey-patch lark_oapi.ws.client.loop 为 daemon 线程的 loop
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
_AGENT_TIMEOUT = 60                     # Agent 推理超时（秒）
_STALE_MSG_THRESHOLD = 300              # 消息超过 5 分钟视为延迟到达（秒）

# ── 内部状态 ─────────────────────────────────────────────────
_ws_client = None  # type: ignore — 实际类型在 _run_ws 中延迟导入
_ws_loop: asyncio.AbstractEventLoop | None = None  # daemon 线程的事件循环，stop() 跨线程调度用
_ws_thread: threading.Thread | None = None
_connected = False
_stop_requested = False  # stop() 置位后，_run_ws() 据此区分正常停止与异常退出
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
    """处理单条飞书消息：时效检查 → 限流 → 取消旧任务 → 异步 agent。

    用户连发消息时，新消息会取消上一个正在进行的推理任务，
    确保机器人始终回答用户最新的问题。
    """
    logger.info("飞书消息 open_id=%s msg_id=%s text=%s", open_id, msg_id, text[:100])

    # 1. 消息为空
    if not text:
        await send_reply(open_id, "请发送有效的问题", msg_id)
        return

    # 2. 时效检查：WebSocket 断线重连后飞书会补投积压消息，过旧的消息不再推理
    if create_time_ms:
        try:
            create_time = datetime.fromtimestamp(int(create_time_ms) / 1000)
            age = (datetime.now() - create_time).total_seconds()
            if age > _STALE_MSG_THRESHOLD:
                logger.warning("飞书消息延迟 %.0f 秒，跳过推理 open_id=%s", age, open_id)
                await send_reply(open_id, "您的消息因网络原因延迟到达，如仍需回答请重新发送", msg_id)
                return
        except (ValueError, TypeError):
            pass

    # 3. 限流检查
    if rate_limiter:
        allowed, retry_after = await rate_limiter.check_by_user_id(open_id)
        if not allowed:
            await send_reply(open_id, f"请求太频繁，请 {retry_after} 秒后再试", msg_id)
            logger.info("飞书限流命中 open_id=%s retry_after=%d", open_id, retry_after)
            return

    # 4. 取消该用户上一个推理任务（同步块，无 await，防止竞态）
    old_task = _active_tasks.get(open_id)
    if old_task is not None and not old_task.done():
        old_task.cancel()
        logger.info("飞书取消旧任务 open_id=%s", open_id)

    # 5. 启动新推理任务（占位回复在 _run_agent 内发送）
    task = asyncio.create_task(_run_agent(open_id, text, msg_id))
    _active_tasks[open_id] = task


async def _run_agent(open_id: str, text: str, msg_id: str) -> None:
    """后台执行 Agent 推理：占位回复 → 推理 → 最终回复。

    被新消息取消时不发送回复。超时/异常时仅当仍是活跃任务才回复。
    """
    current_task = asyncio.current_task()
    try:
        # 1. 占位回复
        await send_reply(open_id, "正在查询中…", msg_id)

        # 2. Agent 推理
        result = await asyncio.wait_for(
            _do_run_chat(open_id, text),
            timeout=_AGENT_TIMEOUT,
        )

        # 3. 最终回复（仅当仍是活跃任务）
        if _active_tasks.get(open_id) is current_task:
            await send_reply(open_id, result, msg_id)
    except asyncio.CancelledError:
        # 被新消息取消，静默退出
        return
    except asyncio.TimeoutError:
        logger.warning("飞书 Agent 超时 open_id=%s", open_id)
        if _active_tasks.get(open_id) is current_task:
            await send_reply(open_id, "处理超时，请简化问题重试", msg_id)
    except Exception:
        logger.exception("飞书 Agent 异常 open_id=%s", open_id)
        if _active_tasks.get(open_id) is current_task:
            await send_reply(open_id, "处理出错，请稍后重试", msg_id)
    finally:
        if _active_tasks.get(open_id) is current_task:
            _active_tasks.pop(open_id, None)


async def _do_run_chat(open_id: str, text: str) -> str:
    """实际调用 run_chat 管道，返回完整 answer 文本。"""
    if not memory_manager or not trace_logger:
        return "服务暂时不可用"

    # DeepSeek API 不可用时 run_chat 内部会抛异常，
    # 由 _run_agent 的 except 捕获并回复"处理出错，请稍后重试"
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
    """初始化飞书 WebSocket 长连接。

    注入外部依赖（memory / trace / rate_limiter），
    然后启动 lark-oapi WebSocket 客户端后台任务。

    如果 FEISHU_ENABLED=False，则跳过（本地开发模式）。
    """
    global memory_manager, trace_logger, rate_limiter
    memory_manager = mem
    trace_logger = trace
    rate_limiter = rl

    if not FEISHU_ENABLED:
        logger.info("飞书已禁用（FEISHU_ENABLED=false），跳过 WebSocket 连接")
        return

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.warning("飞书已启用但缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET，跳过连接")
        return

    logger.info("飞书 WebSocket 长连接启动中…")
    _start_ws_thread()


async def stop() -> None:
    """优雅关闭飞书 WebSocket 长连接。

    分两步:
        1. 通过 run_coroutine_threadsafe 在 daemon 线程的 loop 上执行
           _ws_client._disconnect()，关闭 WebSocket 连接。
        2. 调用 _ws_loop.call_soon_threadsafe(_ws_loop.stop) 停止 loop，
           让 _ws_client.start() 内部的 loop.run_until_complete(_select()) 退出。

    _stop_requested 标志让 _run_ws() 的 except 块区分"正常停止"与"异常退出"。
    """
    global _ws_client, _ws_loop, _connected, _stop_requested
    _connected = False
    _stop_requested = True
    if _ws_client is not None and _ws_loop is not None:
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


def is_connected() -> bool:
    """查询飞书 WebSocket 连接状态。"""
    return _connected


# ── WebSocket 线程管理 ─────────────────────────────────────────

def _run_ws() -> None:
    """在独立线程中运行飞书 WebSocket 客户端（同步阻塞）。

    SDK 的 WsClient.start() 是同步阻塞方法，必须跑在独立线程。
    auto_reconnect=True 时 SDK 内部处理断线重连。

    关于 lark_oapi 模块级 loop 绑定问题及修复方案，
    详见模块顶部 docstring "Event loop 绑定问题" 一节。
    """
    global _ws_client, _ws_loop, _connected, _stop_requested
    global memory_manager, trace_logger, rate_limiter

    # 1. 为 daemon 线程创建专属事件循环
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _ws_loop = _loop

    # 2. 为 daemon 线程创建独立的 Redis 连接实例
    #    主线程（uvicorn）创建的 redis.asyncio 连接池绑定到主线程 loop，
    #    在 daemon 线程跨 loop 使用会静默失败（check_and_clear_summary_flag
    #    的 except 会把 self._redis 设为 None，后续全部走内存 fallback）。
    #    这里在 daemon 线程的 loop 里重新 connect，确保连接绑定到本线程 loop。
    memory_manager = MemoryManager()
    trace_logger = TraceLogger()
    rate_limiter = RateLimiter()
    _loop.run_until_complete(memory_manager.connect())
    _loop.run_until_complete(trace_logger.connect())
    _loop.run_until_complete(rate_limiter.connect())
    logger.info("飞书 daemon 线程 Redis 连接已建立")

    try:
        # 3. 延迟导入 SDK 模块（在 try 内，导入失败也能记录日志）
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from lark_oapi.ws import Client as WsClient
        from lark_oapi.ws import client as _ws_module  # 模块对象，用于 monkey-patch
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandlerBuilder
    except Exception:
        logger.exception("飞书 lark_oapi 导入失败，请检查 lark-oapi 是否正确安装")
        _ws_loop = None
        return

    # 4. 强制将 SDK 模块级 loop 替换为当前线程 loop（防止主线程预导入导致绑定错误）
    _ws_module.loop = _loop

    # 5. 构建事件处理器
    def _handler(data: P2ImMessageReceiveV1) -> None:
        event_data = data.event
        if event_data is None or event_data.message is None:
            logger.info("飞书事件无 message 字段，跳过")
            return

        msg_type = event_data.message.message_type or ""

        # 只处理 text 类型消息（post 是机器人自己发的富文本，跳过避免循环）
        if msg_type != "text":
            logger.info("飞书消息非 text 类型（%s），跳过", msg_type)
            return

        msg_id = event_data.message.message_id or ""
        open_id = ""
        if event_data.sender and event_data.sender.sender_id:
            sender_id_obj = event_data.sender.sender_id
            # 飞书 sender_id 对象可能包含 open_id / user_id / union_id，
            # 优先用 open_id（最稳定，作为 session_id 最合适）
            open_id = getattr(sender_id_obj, "open_id", "") or ""
            if not open_id:
                # 兜底：尝试 user_id
                open_id = getattr(sender_id_obj, "user_id", "") or ""

        text = _parse_message_text(event_data.message.content)
        if not open_id or not text:
            logger.info("飞书消息 open_id 或 text 为空，跳过")
            return

        logger.debug(
            "飞书 sender_type=%s sender_id=%s",
            event_data.sender.sender_type,
            {k: getattr(sender_id_obj, k, None) for k in ("open_id", "user_id", "union_id")},
        )

        # 飞书消息创建时间（毫秒时间戳字符串），用于延迟消息检测
        create_time_ms = getattr(event_data.message, "create_time", "") or ""

        # 调度异步消息处理（_loop 是本线程的事件循环）
        _loop.create_task(_process_message(open_id, text.strip(), msg_id, create_time_ms))

    _builder = EventDispatcherHandlerBuilder(
        encrypt_key="",           # WebSocket 长连接模式无需加密密钥
        verification_token="",    # SDK 内置鉴权，无需手动验签
    )
    _builder.register_p2_im_message_receive_v1(_handler)
    _event_handler = _builder.build()

    # 6. 创建客户端并启动（同步阻塞）
    _ws_client = WsClient(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        event_handler=_event_handler,
        auto_reconnect=True,
    )

    try:
        logger.info("飞书 WebSocket 线程启动")
        _connected = True
        _stop_requested = False
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
        _ws_loop = None


def _start_ws_thread() -> None:
    """启动飞书 WebSocket 后台线程（daemon，进程退出时自动终止）。"""
    global _ws_thread

    if _ws_thread is not None and _ws_thread.is_alive():
        logger.warning("飞书 WebSocket 线程已在运行，跳过重复启动")
        return

    _ws_thread = threading.Thread(target=_run_ws, daemon=True, name="feishu-ws")
    _ws_thread.start()
