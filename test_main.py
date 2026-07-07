import asyncio
from llm_client import cloud_chat
from core.router import classify_intent
from core.react_loop import run_react_loop
from prompts.direct_answer import SYSTEM_PROMPT_DIRECT_ANSWER


async def llm_direct_answer(user_message: list) -> None:
    user_question = user_message[-1]["content"] if user_message else ""
    system_prompt = SYSTEM_PROMPT_DIRECT_ANSWER.replace("{user_question}", user_question)
    messages = [{"role": "system", "content": system_prompt}]

    async for chunk in cloud_chat(messages):
        if chunk["type"] == "text":
            print(chunk["content"], end="", flush=True)
        elif chunk["type"] == "done":
            print(f"\n[{chunk['finish_reason']}]")



async def main():
    user_message = "最近AI眼镜板块如何？该板块方向下近期哪个基金收益更好，值得建仓"
    user_input = [{"role":"user","content":user_message}]
    decision = classify_intent(user_input)

    print("\n",f'执行{decision["category"]}路径',"\n")

    if decision["category"] == "DirectAnswer":
        await llm_direct_answer(user_input)
    elif decision["category"] == "ReAct":
        await run_react_loop(user_input, decision["tools_needed"])
    # else:
    #     reply = run_rewoo(user_input, decision["tools_needed"])

    # async for chunk in cloud_chat(messages):
    #     if chunk["type"] == "text":
    #         print(chunk["content"], end="", flush=True)
    #     elif chunk["type"] == "done":
    #         reason = chunk["finish_reason"]
    #         print(f"\n[{reason}]")
    #         break

asyncio.run(main())