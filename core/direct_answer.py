import time
from typing import Awaitable, Callable, Optional

from core.history_formatter import format_history_dialogue
from core.trace import TraceLogger
from llm_client import cloud_chat
from prompts.direct_answer import SYSTEM_PROMPT_DIRECT_ANSWER


OutputCallback = Callable[[str], Awaitable[None]]


async def run_direct_answer(
    user_message: list,
    history: list[dict],
    has_context: bool,
    trace: TraceLogger,
    session_id: str,
    *,
    on_chunk: Optional[OutputCallback] = None,
) -> str:
    """直接回答模式：不调用工具，直接用 LLM 知识作答（有上下文时综合历史数据）。

    on_chunk=None → CLI 模式（print），否则逐 chunk 回调。
    """
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
    async for chunk in cloud_chat(messages):
        if chunk["type"] == "text":
            buffer += chunk["content"]
            if on_chunk:
                await on_chunk(chunk["content"])
            else:
                print(chunk["content"], end="", flush=True)
        elif chunk["type"] == "done":
            llm_usage = chunk.get("usage")

    latency = (time.perf_counter() - t0) * 1000
    if not on_chunk:
        print()

    await trace.log(session_id, step=0, event="react.final_answer",
                    output=buffer[:500], latency_ms=latency, tokens=llm_usage)

    return buffer
