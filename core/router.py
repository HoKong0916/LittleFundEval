import json
from llm_client import local_chat
from prompts.router import SYSTEM_PROMPT_ROUTER
# from config.tools_yaml import get_tools_for_prompt

def classify_intent(user_message: list) -> dict:
    # tools_desc = get_tools_for_prompt()  # 从 tools.yaml 生成简短描述
    # system_prompt = SYSTEM_PROMPT_ROUTER.format(tools=tools_desc)

    user_question = user_message[-1]["content"] if user_message else ""
    system_prompt = SYSTEM_PROMPT_ROUTER.replace("{user_question}", user_question)
    messages = [{"role":"system","content":system_prompt}]


    response = local_chat(
        messages = messages
    )
    result = json.loads(response)

    return {
        "category": result["category"],
        "tools_needed": result.get("tools_needed", []),
        "reasoning": result.get("reasoning", "")
    }