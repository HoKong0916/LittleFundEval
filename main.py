"""FastAPI 入口 —— 健康检查 + trace 回溯 + 非流式聊天 + 飞书机器人。

启动:
    uvicorn main:app --port 8000

端点:
    GET  /health          — 健康检查
    GET  /trace/{sid}     — 会话调用链 JSON（Redis 储存 24h TTL）
    POST /chat            — 非流式 JSON 聊天（Bearer Token 鉴权 + 限流）

鉴权模型:
    无鉴权 endpoint  →  /health, /trace/{sid}
    Bearer Token    →  /chat
    Token 等级: admin (不限流), visitor (滑动窗口 5次/分钟)

飞书通道:
    通过 WebSocket 长连接接收消息，不走 HTTP 端点。
    session_id = 飞书 open_id（由 channels/feishu/bot.py 管理）。
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel

# 日志配置：必须在所有业务模块导入前设置，确保各模块 logger.info 可见
# 二选一：配置 LOG_FILE 时仅写文件（RotatingFileHandler 轮转），否则仅写终端
_log_format = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_root = logging.getLogger()
_root.setLevel(logging.INFO)

from config import LOG_FILE, LOG_FILE_MAX_BYTES, LOG_FILE_BACKUP_COUNT

if LOG_FILE:
    _log_dir = os.path.dirname(LOG_FILE)
    if _log_dir:  # 路径含目录时先创建，避免 RotatingFileHandler 因目录不存在失败
        os.makedirs(_log_dir, exist_ok=True)
    _file = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    _file.setFormatter(_log_format)
    _root.addHandler(_file)
    logging.getLogger(__name__).info(
        "文件日志已启用: %s (maxBytes=%d, backupCount=%d)",
        LOG_FILE, LOG_FILE_MAX_BYTES, LOG_FILE_BACKUP_COUNT,
    )
else:
    _stream = logging.StreamHandler()
    _stream.setFormatter(_log_format)
    _root.addHandler(_stream)

from channels.feishu import bot as feishu_bot
from core.auth import TokenInfo, verify_token
from core.chat import run_chat
from core.memory import MemoryManager
from core.rate_limit import RateLimiter
from core.trace import TraceLogger


# ── 全局实例（lifespan 管理生命周期）──────────────────────────
trace_logger = TraceLogger()
memory_manager = MemoryManager()
rate_limiter = RateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：连接 Redis → 连接飞书 → 初始化 DB。"""
    await trace_logger.connect()
    await memory_manager.connect()
    await rate_limiter.connect()
    await feishu_bot.start(memory_manager, trace_logger, rate_limiter)
    yield
    await feishu_bot.stop()
    await trace_logger.disconnect()
    await memory_manager.disconnect()
    await rate_limiter.disconnect()


app = FastAPI(title="Little Gambling", lifespan=lifespan)


# ── 依赖 ──────────────────────────────────────────────────────

async def check_rate_limit(token_info: TokenInfo = Depends(verify_token)) -> TokenInfo:
    """限流依赖：admin 跳过，visitor 按 token.user_id 限流。"""
    allowed, retry_after = await rate_limiter.check(token_info)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求频率超限，{retry_after} 秒后再试",
        )
    return token_info


# ── Request / Response Model ──────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    category: str


# ── Trace 端点 ────────────────────────────────────────────────

@app.get("/trace/{session_id}")
async def get_trace(session_id: str):
    """返回指定会话的完整调用链 JSON。"""
    steps = await trace_logger.get_trace(session_id)
    if not steps:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 无 trace 数据（可能已过期）")
    return {
        "session_id": session_id,
        "steps": steps,
        "count": len(steps),
    }


# ── 健康检查 ──────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── 非流式 JSON 聊天 ──────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    token_info: TokenInfo = Depends(check_rate_limit),
):
    """非流式 JSON 聊天端点。

    请求:
        POST /chat
        Authorization: Bearer sk-xxx
        {"message": "大摩数字经济混合C 近一个月表现怎么样？"}

    响应:
        200: {"answer": "...", "category": "REWOO"}
        400: 消息为空
        401: 鉴权失败
        409: 用户处理中
        429: 限流
        500: 服务异常
        504: 推理超时
    """
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 字段不能为空")

    # session_id = token 的 user_id（不再生成随机 UUID）
    session_id = token_info.user_id

    await trace_logger.log(session_id, step=0, event="api.request",
                           input={"user_id": token_info.user_id,
                                  "tier": token_info.tier,
                                  "message": message[:200]})

    # 会话锁：同一 user_id 同一时间只允许一个请求处理
    if not await memory_manager.acquire_session_lock(session_id):
        raise HTTPException(status_code=409, detail="该用户正在处理中，请稍后重试")

    try:
        result = await asyncio.wait_for(
            run_chat(session_id, message, memory_manager, trace_logger),
            timeout=60,
        )
        return ChatResponse(answer=result["answer"], category=result["category"])
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="处理超时，请简化问题重试")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="处理出错，请稍后重试")
    finally:
        await memory_manager.release_session_lock(session_id)


# ── 飞书连接状态 ──────────────────────────────────────────────

@app.get("/feishu/status")
async def feishu_status():
    """查询飞书 WebSocket 连接状态。"""
    return {"connected": feishu_bot.is_connected()}
