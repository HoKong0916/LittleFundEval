"""FastAPI 入口 —— 健康检查 + trace 回溯 + SSE 流式聊天。

启动:
    uvicorn main:app --port 8000

端点:
    GET  /health          — 健康检查
    GET  /trace/{sid}     — 会话调用链 JSON（Redis 储存 24h TTL）
    POST /chat/stream     — SSE 流式聊天（Bearer Token 鉴权 + 限流）

鉴权模型:
    无鉴权 endpoint  →  /health, /trace/{sid}
    Bearer Token    →  /chat/stream
    Token 等级: admin (不限流), visitor (滑动窗口 5次/分钟)
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
# FastAPI 单进程模型下用模块级全局实例是安全的，
# 后续扩展多 worker 时需改为每个 worker 独立实例。
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
      data: {"type":"chunk","content":"..."}     — LLM 增量输出
      data: {"type":"done","category":"REWOO"}   — 正常结束
      data: {"type":"error","detail":"..."}      — 异常

    架构: Queue 生产者-消费者解耦
      runner()  →  生产者，执行 run_chat（可能耗时 30s+），
                   逐 chunk 推入 queue，完成后推 sentinel
      event_stream() → 消费者，从 queue 拉取并转为 SSE 格式，
                   遇到 sentinel 退出
      两个协程通过 asyncio.Queue 解耦，流式输出不等待完整回答。
    """
    # 记录请求来源（用于审计）
    await trace_logger.log(body.session_id, step=0, event="api.request",
                           input={"user_id": token_info.user_id,
                                  "tier": token_info.tier,
                                  "message": body.message[:200]})

    # ── 会话锁：同一 session_id 同一时间只允许一个请求处理 ──
    # 防止并发写入 Redis 导致消息乱序/丢失，
    # SETNX 是原子操作，锁自动 60s 过期（防死锁）。
    if not await memory_manager.acquire_session_lock(body.session_id):
        raise HTTPException(status_code=409, detail="该会话正在处理中，请稍后重试")

    # asyncio.Queue: 生产者(runner) → 消费者(event_stream) 的解耦桥梁
    queue: asyncio.Queue = asyncio.Queue()

    async def on_chunk(text: str):
        """LLM 增量回调：每收到一个 token 就推入 queue。"""
        await queue.put(("chunk", text))

    async def runner():
        """后台执行 run_chat 管道，完成后推送结果并释放锁。

        Queue 协议:
          ("chunk", str)     — LLM 增量文本
          ("done", dict)     — 正常结束，payload = {"answer": ..., "category": ...}
          ("error", str)     — 异常
          None               — 哨兵值，通知 event_stream 关闭连接
        """
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
            await queue.put(None)  # 哨兵：无论如何都要解除 event_stream 的阻塞
            await memory_manager.release_session_lock(body.session_id)

    async def event_stream():
        """SSE 生成器：从 queue 拉取消息，格式化为 SSE 事件。

        用 asyncio.create_task 启动 runner 的原因：
        event_stream 是同步生成器模式的异步协程，
        必须先 yield 出 Response 才能让客户端开始接收数据，
        所以 runner 必须作为后台任务启动，不能在 event_stream 内部 await。
        """
        asyncio.create_task(runner())
        while True:
            item = await queue.get()
            if item is None:                     # 哨兵：runner 已退出
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
