from llm_client import cloud_chat
from prompts.direct_answer import SYSTEM_PROMPT_DIRECT_ANSWER


def _flatten_history(history: list[dict]) -> str:
    """把历史对话扁平化为纯文本，保留关键数据供模型综合。"""
    if not history:
        return "（无历史对话）"
    lines = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"助手：{content}")
    return "\n".join(lines)


async def run_direct_answer(user_message: list, history: list[dict], has_context: list[dict] | bool) -> str:
    """直接回答模式：不调用工具，直接用 LLM 知识作答（有上下文时综合历史数据）。"""
    user_question = user_message[-1]["content"] if user_message else ""

    # 注入历史上下文到 system prompt，让模型明确知道要综合哪些历史数据
    history_text = _flatten_history(history) if has_context else "（无历史对话）"

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
