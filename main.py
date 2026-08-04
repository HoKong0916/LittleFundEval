"""FastAPI 入口 —— 飞书 WebSocket 为主交互通道，REST 仅用于开发调试与面试演示。

启动: uvicorn main:app --port 8000
端点:
    GET  /health            健康检查
    GET  /feishu/status     飞书 WebSocket 连接状态
    GET  /trace/{sid}       查看调用链（面试演示可解释性核心）
    POST /chat              极简调试端点，无鉴权/无限流/无会话锁
飞书走 WebSocket 收消息，session_id = open_id；API 调试端点 session_id 由请求体传入。
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException
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
from core.chat import run_chat
from core.memory import MemoryManager
from core.rate_limit import RateLimiter
from core.trace import TraceLogger


# ── 全局实例 ──────────────────────────────────────────────────

trace_logger = TraceLogger()
memory_manager = MemoryManager()
rate_limiter = RateLimiter()


# ── 应用生命周期 ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：连接 Redis → 启动飞书 WebSocket。"""
    await trace_logger.connect()
    await memory_manager.connect()
    await rate_limiter.connect()
    # 飞书：启动 WebSocket 长连接
    await feishu_bot.start(memory_manager, trace_logger, rate_limiter)
    yield
    # 飞书：关闭 WebSocket 长连接
    await feishu_bot.stop()
    await trace_logger.disconnect()
    await memory_manager.disconnect()
    await rate_limiter.disconnect()


app = FastAPI(title="Little Gambling", lifespan=lifespan)


# ── API 模型 ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "debug"   # 调试用：切换会话可测记忆/上下文，默认 debug


class ChatResponse(BaseModel):
    answer: str
    category: str


# ── API 端点：GET /health ─────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── API 端点：GET /trace/{session_id} ─────────────────────────

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


# ── API 端点：POST /chat（开发调试用，无鉴权/无限流/无会话锁）──

@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    """极简调试端点：直接跑 run_chat 管道，返回 {answer, category}。

    仅供开发自测与面试演示调用链用，生产交互走飞书机器人。
    session_id 由请求体传入，切换可测会话记忆与上下文延续。
    """
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 字段不能为空")

    session_id = body.session_id

    await trace_logger.log(session_id, step=0, event="api.request",
                           input={"session_id": session_id, "message": message[:200]})

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


# ── 飞书端点：GET /feishu/status ──────────────────────────────

@app.get("/feishu/status")
async def feishu_status():
    """查询飞书 WebSocket 连接状态。"""
    return feishu_bot.get_connection_status()
