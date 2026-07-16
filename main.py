"""FastAPI 入口 —— trace 回溯端点 + 对话 API（后续扩展）。

启动: uvicorn main:app --port 8000
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from core.trace import TraceLogger
from core.memory import MemoryManager

# ── 全局实例（lifespan 管理生命周期）──────────────────────────
trace_logger = TraceLogger()
memory_manager = MemoryManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时连接 Redis，关闭时断开。"""
    await trace_logger.connect()
    await memory_manager.connect()
    yield
    await trace_logger.disconnect()
    await memory_manager.disconnect()


app = FastAPI(title="Little Gambling", lifespan=lifespan)


# ── Trace 端点 ────────────────────────────────────────────────

@app.get("/trace/{session_id}")
async def get_trace(session_id: str):
    """返回指定会话的完整调用链 JSON。

    trace 数据保留 24 小时（Redis TTL），超时后返回空列表。
    """
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
