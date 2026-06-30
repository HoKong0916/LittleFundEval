import re

from llm_client import cloud_chat
from prompts.react import SYSTEM_PROMPT_REACT


MAX_STEPS = 5

_ACTION_RE = re.compile(r"Action:\s*(\w+)\((.*)\)")
_PARAM_RE = re.compile(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"')
_THOUGHT_RE = re.compile(r"Thought:\s*(.*)")
_FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)


def parse_step(buffer: str) -> dict:
    """从 LLM 输出中解析 Thought / Action / Final Answer（完整 buffer 兜底解析）。"""
    thought = ""
    thought_m = _THOUGHT_RE.search(buffer)
    if thought_m:
        thought = thought_m.group(1).strip()

    final_m = _FINAL_RE.search(buffer)
    if final_m:
        return {"thought": thought, "final_answer": final_m.group(1).strip()}

    action_m = _ACTION_RE.search(buffer)
    if action_m:
        tool_name = action_m.group(1).strip()
        params = dict(_PARAM_RE.findall(action_m.group(2)))
        return {"thought": thought, "tool": tool_name, "params": params}

    return {"thought": thought, "parse_error": True}


def _try_parse_action(buffer: str) -> dict | None:
    """增量检测：Action 闭括号到达时立即返回 parsed dict，否则返回 None。

    不检测 Final Answer —— Final Answer 需要流式输出完整内容，不应提前截断。
    """
    action_m = _ACTION_RE.search(buffer)
    if not action_m:
        return None
    tool_name = action_m.group(1).strip()
    params = dict(_PARAM_RE.findall(action_m.group(2)))
    thought_m = _THOUGHT_RE.search(buffer)
    thought = thought_m.group(1).strip() if thought_m else ""
    return {"thought": thought, "tool": tool_name, "params": params}


async def run_react_loop(user_message: list, tools_needed: list) -> None:
    user_question = user_message[-1]["content"] if user_message else ""
    system_prompt = (
        SYSTEM_PROMPT_REACT
        .replace("{user_question}", user_question)
        .replace("{initial_tools}", str(tools_needed))
    )
    messages = [{"role": "system", "content": system_prompt}]

    for step in range(1, MAX_STEPS + 1):
        print(f"\n{'─' * 50}")
        print(f"[步骤 {step}/{MAX_STEPS}] ", end="", flush=True)

        buffer = ""
        parsed = None

        async for chunk in cloud_chat(messages):
            if chunk["type"] == "text":
                buffer += chunk["content"]
                # 流式实时打印 —— 用户看到模型逐 token 输出 Thought/Action/Final Answer
                print(chunk["content"], end="", flush=True)

                # 增量检测：Action 闭括号一到齐，立即截断流式接收，准备 dispatch 工具
                parsed = _try_parse_action(buffer)
                if parsed:
                    break

            elif chunk["type"] == "tool_calls":
                # 模型返回原生 function call（兜底路径）
                calls = chunk["calls"]
                if calls:
                    parsed = {
                        "thought": "",
                        "tool": calls[0]["name"],
                        "params": calls[0]["arguments"],
                    }
                break

            elif chunk["type"] == "done":
                break

        print()  # 流式输出后换行

        # 兜底：流正常结束但未通过增量检测捕获到（如 Final Answer 场景）
        if parsed is None:
            parsed = parse_step(buffer)

        if "parse_error" in parsed:
            print("[错误] 模型输出无法解析，终止")
            break

        if "final_answer" in parsed:
            # Final Answer 内容已在流式中实时打印，此处仅结束循环
            break

        # 工具调用
        params_str = ", ".join(f'{k}="{v}"' for k, v in parsed["params"].items())
        print(f"⏳ → {parsed['tool']}({params_str})")

        observation = ""  # TODO: await execute_tool(parsed["tool"], parsed["params"])
        if observation:
            print(f"📋 {observation}")

        messages.append({"role": "assistant", "content": buffer})
        messages.append({"role": "user", "content": f"Observation: {observation}"})
