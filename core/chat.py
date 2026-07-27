"""对话编排管道 —— 将 test_main.py 的 6 步管道抽成独立函数。

用法:
    async with MemoryManager() as memory, TraceLogger() as trace:
        result = await run_chat(
            session_id, user_message, memory, trace,
            on_chunk=my_callback,   # None = CLI print 模式
        )
"""

from typing import Awaitable, Callable, Optional

from core.router import classify_intent
from core.react_loop import run_react_loop
from core.rewoo_loop import run_rewoo_loop
from core.topic import is_same_topic
from core.direct_answer import run_direct_answer
from core.memory import MemoryManager
from core.trace import TraceLogger
from core.summarizer import summarize_session
from config import count_tokens, MAX_TOKEN_THRESHOLD

OutputCallback = Callable[[str], Awaitable[None]]


async def run_chat(
    session_id: str,
    user_message: str,
    memory: MemoryManager,
    trace: TraceLogger,
    *,
    on_chunk: Optional[OutputCallback] = None,
) -> dict:
    """执行一次完整的对话管道。

    返回 {"answer": str, "category": str}。
    on_chunk=None 时，输出退化为 print()（CLI 模式）。
    """
    # ── 摘要检查（N+1 轮启动时）───
    need_summary = await memory.check_and_clear_summary_flag(session_id)
    if need_summary:
        await summarize_session(memory, session_id)

    # ── 加载历史 ──
    history = await memory.load_messages(session_id)
    has_context = bool(history) and await is_same_topic(user_message, history)

    # ── 路由 ──
    decision = await classify_intent(
        [{"role": "user", "content": user_message}],
        history if has_context else None,
        trace=trace, session_id=session_id,
    )

    # ── 执行 ──
    msg_list = [{"role": "user", "content": user_message}]
    category = decision["category"]

    if category == "DirectAnswer":
        answer = await run_direct_answer(
            msg_list, history, has_context, trace, session_id,
            on_chunk=on_chunk,
        )
    elif category == "ReAct":
        answer = await run_react_loop(
            msg_list, decision["tools_needed"], history, has_context,
            trace, session_id, on_chunk=on_chunk,
        )
    elif category == "REWOO":
        answer = await run_rewoo_loop(
            msg_list, decision["tools_needed"], history, has_context,
            trace, session_id, on_chunk=on_chunk,
        )
    else:
        answer = ""

    # ── 存入记忆 ──
    if answer:
        await memory.append_message(session_id, {"role": "user", "content": user_message})
        await memory.append_message(session_id, {"role": "assistant", "content": answer})

        current_messages = await memory.load_messages(session_id)
        current_total = sum(count_tokens(m["content"]) for m in current_messages)
        if current_total > MAX_TOKEN_THRESHOLD:
            await memory.set_summary_flag(session_id)
    else:
        await trace.log(session_id, step=0, event="session.no_answer")

    return {"answer": answer, "category": category}
