from core.history_formatter import format_history_dialogue
from llm_client import cloud_chat
from prompts.direct_answer import SYSTEM_PROMPT_DIRECT_ANSWER


async def run_direct_answer(user_message: list, history: list[dict], has_context: list[dict] | bool) -> str:
    """直接回答模式：不调用工具，直接用 LLM 知识作答（有上下文时综合历史数据）。"""
    user_question = user_message[-1]["content"] if user_message else ""

    # 注入历史上下文到 system prompt，让模型明确知道要综合哪些历史数据
    history_text = format_history_dialogue(history) if has_context else "（无历史对话）"

    system_prompt = (
        SYSTEM_PROMPT_DIRECT_ANSWER
        .replace("{history_context}", history_text)
        .replace("{user_question}", user_question)
    )

    messages = [{"role": "system", "content": system_prompt}]

    buffer = ""
    async for chunk in cloud_chat(messages):
        if chunk["type"] == "text":
            buffer += chunk["content"]
            print(chunk["content"], end="", flush=True)
        elif chunk["type"] == "done":
            emoji = {"stop": "✅", "tool_calls": "🔧"}.get(chunk["finish_reason"], "")
            print(f"\n{emoji} [{chunk['finish_reason']}]")
    return buffer
