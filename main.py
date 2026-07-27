"""FastAPI 入口 —— 健康检查 + trace 回溯 + SSE 流式聊天。

启动: uvicorn main:app --port 8000
"""

import json
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.trace import TraceLogger
from core.memory import MemoryManager
from core.rate_limit import RateLimiter
from core.auth import TokenInfo, verify_token
from core.chat import run_chat


# ── 全局实例（lifespan 管理生命周期）──────────────────────────
trace_logger = TraceLogger()
memory_manager = MemoryManager()
rate_limiter = RateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：连接 Redis + 初始化 DB。"""
    await trace_logger.connect()
    await memory_manager.connect()
    await rate_limiter.connect()
    yield
    await trace_logger.disconnect()
    await memory_manager.disconnect()
    await rate_limiter.disconnect()


app = FastAPI(title="Little Gambling", lifespan=lifespan)


# ── 依赖 ──────────────────────────────────────────────────────

async def check_rate_limit(token_info: TokenInfo = Depends(verify_token)) -> TokenInfo:
    """限流依赖：admin 跳过，visitor 每分钟 5 次。"""
    allowed, retry_after = await rate_limiter.check(token_info)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求频率超限，{retry_after} 秒后再试",
        )
    return token_info


# ── Request / Response Model ──────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str


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


# ── SSE 流式聊天 ──────────────────────────────────────────────

@app.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    token_info: TokenInfo = Depends(check_rate_limit),
):
    """SSE 流式聊天端点。

    事件格式:
      data: {"type":"chunk","content":"..."}    —— 增量文本
      data: {"type":"done","category":"REWOO"}  —— 结束
    """
    # 记录请求用户
    await trace_logger.log(body.session_id, step=0, event="api.request",
                           input={"user_id": token_info.user_id,
                                  "tier": token_info.tier,
                                  "message": body.message[:200]})

    # 会话锁：同一 session_id 串行化
    if not await memory_manager.acquire_session_lock(body.session_id):
        raise HTTPException(status_code=409, detail="该会话正在处理中，请稍后重试")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_chunk(text: str):
        await queue.put(("chunk", text))

    async def runner():
        try:
            result = await run_chat(
                body.session_id, body.message,
                memory_manager, trace_logger,
                on_chunk=on_chunk,
            )
            await queue.put(("done", result))
        except Exception as e:
            await queue.put(("error", str(e)))
        finally:
            await queue.put(None)  # sentinel
            await memory_manager.release_session_lock(body.session_id)

    async def event_stream():
        asyncio.create_task(runner())
        while True:
            item = await queue.get()
            if item is None:
                break
            typ, payload = item
            if typ == "chunk":
                yield f"data: {json.dumps({'type': 'chunk', 'content': payload}, ensure_ascii=False)}\n\n"
            elif typ == "done":
                response = json.dumps(
                    {"type": "done", "category": payload["category"]},
                    ensure_ascii=False,
                )
                yield f"data: {response}\n\n"
            elif typ == "error":
                yield f"data: {json.dumps({'type': 'error', 'detail': payload}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
