"""对话编排管道 —— 摘要检查 → 路由分类 → 执行器 → 存入记忆。"""

from config import MAX_TOKEN_THRESHOLD, count_tokens
from core.direct_answer import run_direct_answer
from core.memory import MemoryManager
from core.react_loop import run_react_loop
from core.rewoo_loop import run_rewoo_loop
from core.router import classify_intent
from core.summarizer import summarize_session
from core.topic import is_same_topic
from core.trace import TraceLogger


async def run_chat(
    session_id: str,
    user_message: str,
    memory: MemoryManager,
    trace: TraceLogger,
) -> dict:
    """执行对话管道，返回 {"answer": str, "category": str}。

    session_id 飞书用 open_id，API 调试用 session_id（请求体传入）。
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
        )
    elif category == "ReAct":
        answer = await run_react_loop(
            msg_list, decision["tools_needed"], history, has_context,
            trace, session_id,
        )
    elif category == "REWOO":
        answer = await run_rewoo_loop(
            msg_list, decision["tools_needed"], history, has_context,
            trace, session_id,
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
