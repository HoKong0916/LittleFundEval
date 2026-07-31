"""直接回答模式 —— 不调工具，纯 LLM 知识 + 历史对话综合作答。

适用场景由 router 判断：投资知识科普、概念解释、策略讨论等不涉及实时数据的对话。
"""

import time

from core.history_formatter import format_history_dialogue
from core.trace import TraceLogger
from llm_client import cloud_chat
from prompts.direct_answer import SYSTEM_PROMPT_DIRECT_ANSWER


async def run_direct_answer(
    user_message: list,
    history: list[dict],
    has_context: bool,
    trace: TraceLogger,
    session_id: str,
) -> str:
    """不调工具，用 LLM 知识作答（有上下文时综合历史数据）。"""
    user_question = user_message[-1]["content"] if user_message else ""

    history_text = format_history_dialogue(history) if has_context else "（无历史对话）"

    system_prompt = (
        SYSTEM_PROMPT_DIRECT_ANSWER
        .replace("{history_context}", history_text)
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_question},
    ]

    await trace.log(session_id, step=0, event="router.direct_answer",
                    input={"question": user_question[:200]})

    t0 = time.perf_counter()
    buffer = ""
    llm_usage = None
    async for chunk in cloud_chat(messages, session_id=session_id):
        if chunk["type"] == "text":
            buffer += chunk["content"]
        elif chunk["type"] == "done":
            llm_usage = chunk.get("usage")

    latency = (time.perf_counter() - t0) * 1000

    await trace.log(session_id, step=0, event="react.final_answer",
                    output=buffer[:500], latency_ms=latency, tokens=llm_usage)

    return buffer
