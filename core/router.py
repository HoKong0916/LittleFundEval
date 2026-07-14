import json
from llm_client import local_chat
from prompts.router import SYSTEM_PROMPT_ROUTER
from tools import tools_prompt_json

async def classify_intent(user_message: list) -> dict:
    """用本地 LLM 将用户问题分类为 DirectAnswer 或 ReAct，并给出所需工具列表。"""
    user_question = user_message[-1]["content"] if user_message else ""
    system_prompt = SYSTEM_PROMPT_ROUTER.replace("{tools_json}", tools_prompt_json()).replace("{user_question}", user_question)
    messages = [{"role":"system","content":system_prompt}]


    response = await local_chat(
        messages = messages
    )
    result = json.loads(response)

    return {
        "category": result["category"],
        "tools_needed": result.get("tools_needed", []),
        "reasoning": result.get("reasoning", "")
    }