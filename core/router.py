import json
from llm_client import local_chat
from prompts.router import SYSTEM_PROMPT_ROUTER
from tools import tools_prompt_json

def _flatten_history(history: list[dict]) -> str:
    """把历史对话扁平化为纯文本摘要，避免 assistant role 干扰分类器。"""
    if not history:
        return ""
    lines = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")[:200]  # 截断，防止太长
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"助手：{content}")
    return "\n".join(lines)


async def classify_intent(user_message: list, history: list[dict] | None = None) -> dict:
    """用本地 LLM 将用户问题分类为 DirectAnswer 或 ReAct，并给出所需工具列表。

    传入 history 时，LLM 可感知对话中已有哪些数据，避免对"仅需基于已有数据给建议"的追问误判为需要调工具。
    """
    user_question = user_message[-1]["content"] if user_message else ""

     # 历史扁平化为纯文本，不作为独立 message
    history_text = _flatten_history(history) if history else ""

    system_prompt = (
        SYSTEM_PROMPT_ROUTER
        .replace("{tools_json}", tools_prompt_json())
        .replace("{user_question}", user_question)
    )

    # 替换 history_context 占位符
    if history_text:
        system_prompt = system_prompt.replace(
            "{history_context}",
            f"\n## 历史对话摘要（仅供判断上下文，你不是对话参与者）\n{history_text}\n"
        )
    else:
        system_prompt = system_prompt.replace("{history_context}", "")

    messages = [{"role": "system", "content": system_prompt}]

    response = await local_chat(messages=messages)
    result = json.loads(response)

    return {
        "category": result["category"],
        "tools_needed": result.get("tools_needed", []),
        "reasoning": result.get("reasoning", "")
    }