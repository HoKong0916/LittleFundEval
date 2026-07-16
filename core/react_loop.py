import json
import re
import time

from llm_client import cloud_chat
from prompts.react import SYSTEM_PROMPT_REACT
from tools import tools_prompt_json
from core.dispatch import dispatch_tool
from core.trace import TraceLogger


MAX_STEPS = 5

_ACTION_RE = re.compile(r"Action:\s*(\w+)\((.*)\)")
_PARAM_RE = re.compile(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"')
_ARRAY_PARAM_RE = re.compile(r'(\w+)\s*=\s*(\[[^\]]*\])')
_THOUGHT_RE = re.compile(r"Thought:\s*(.*)")
_FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)


def _parse_params(raw: str) -> dict:
    """解析 Action 参数，同时支持字符串值和数组值。"""
    params = dict(_PARAM_RE.findall(raw))
    for m in _ARRAY_PARAM_RE.finditer(raw):
        try:
            params[m.group(1)] = json.loads(m.group(2))
        except json.JSONDecodeError:
            pass
    return params


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
        params = _parse_params(action_m.group(2))
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
    params = _parse_params(action_m.group(2))
    thought_m = _THOUGHT_RE.search(buffer)
    thought = thought_m.group(1).strip() if thought_m else ""
    return {"thought": thought, "tool": tool_name, "params": params}


async def run_react_loop(
    user_message: list,
    tools_needed: list,
    history: list[dict],
    has_context: bool,
    trace: TraceLogger,
    session_id: str,
) -> str:
    """ReAct 执行器：Thought → Action → Observation 循环。

    Thought/Action/Observation 原文仅写入 trace JSON 日志，
    终端只展示人类可读的进度提示（DEBUG_TRACE=1 时）。
    """
    user_question = user_message[-1]["content"] if user_message else ""
    system_prompt = (
        SYSTEM_PROMPT_REACT
        .replace("{tools_json}", tools_prompt_json())
        .replace("{user_question}", user_question)
        .replace("{initial_tools}", str(tools_needed))
    )

    if has_context:
        system_prompt += (
            "\n\n## 历史对话（上下文参考）\n"
            "以下是之前的对话记录，仅供你判断哪些数据已有。"
            "但用户当前问题可能包含历史中不存在的数据，"
            "你必须通过工具获取最新数据，不能直接复制历史回答。"
        )

    messages = [{"role": "system", "content": system_prompt}]
    if has_context:
        messages.extend(history)

    final_answer = ""
    for step in range(1, MAX_STEPS + 1):
        t_step = time.perf_counter()

        await trace.log(session_id, step=step, event="react.step.start",
                        input={"step": step, "max_steps": MAX_STEPS})

        buffer = ""
        parsed = None
        llm_usage = None

        # ── 流式 LLM 调用 ──────────────────────────────────────
        # 两种提前终止路径：
        #   1. 增量检测到 Action 闭括号 → 立即截断流，进入工具调用
        #   2. 模型返回原生 tool_calls → 兜底路径，无需解析格式
        # 正常结束（done）→ 进入兜底解析（Final Answer 场景）
        gen = cloud_chat(messages)
        try:
            async for chunk in gen:
                if chunk["type"] == "text":
                    buffer += chunk["content"]

                    # 增量检测：Action 闭括号一到齐，立即截断流式接收
                    # 注意：只检测 Action，不检测 Final Answer ——
                    #   Final Answer 需要流式输出完整内容给用户看，不应提前截断
                    parsed = _try_parse_action(buffer)
                    if parsed:
                        break

                elif chunk["type"] == "tool_calls":
                    # 原生 function call 兜底：模型未按 ReAct 格式输出但发了 tool_call
                    calls = chunk["calls"]
                    if calls:
                        parsed = {
                            "thought": "",
                            "tool": calls[0]["name"],
                            "params": calls[0]["arguments"],
                        }
                    break

                elif chunk["type"] == "done":
                    llm_usage = chunk.get("usage")
                    break
        finally:
            await gen.aclose()

        llm_latency = (time.perf_counter() - t_step) * 1000

        # 兜底：流正常结束（非截断），用完整 buffer 做一次全量解析
        # 处理场景：Final Answer、格式不规范但包含 Action 的输出
        if parsed is None:
            parsed = parse_step(buffer)

        # 记录 LLM 调用 trace（buffer 中含完整的 Thought/Action/Final Answer 原文）
        await trace.log(session_id, step=step, event="react.llm_call",
                        input={"buffer": buffer},
                        output=parsed, latency_ms=llm_latency, tokens=llm_usage)

        if "parse_error" in parsed:
            await trace.log(session_id, step=step, event="react.parse_error",
                            input={"buffer": buffer})
            final_answer = "[错误] 模型输出无法解析"
            break

        if "final_answer" in parsed:
            # 拦截：路由明确需要工具但第 1 步就直接回答 → 注入纠正提示
            # 防止模型跳过数据获取直接基于训练数据编造答案
            if step == 1 and tools_needed:
                await trace.log(session_id, step=step, event="react.skip_guard",
                                input=parsed)
                messages.append({"role": "assistant", "content": buffer})
                messages.append({"role": "user", "content":
                    "你不能直接回答。请先调用工具获取数据——"
                    "用户问的数据可能不在历史中。"
                })
                continue
            final_answer = parsed["final_answer"]
            # Final Answer 是用户可见的最终输出，打印出来
            print(final_answer)
            await trace.log(session_id, step=step, event="react.final_answer",
                            output=final_answer)
            break

        # ── 工具调用 ──
        tool_name = parsed["tool"]
        tool_t0 = time.perf_counter()

        await trace.log(session_id, step=step, event="react.tool_call",
                        input={"tool_name": tool_name, "params": parsed["params"]})

        observation = await dispatch_tool(tool_name, parsed["params"])
        tool_latency = (time.perf_counter() - tool_t0) * 1000

        await trace.log(session_id, step=step, event="react.tool_result",
                        input={"tool_name": tool_name},
                        output=observation, latency_ms=tool_latency)

        messages.append({"role": "assistant", "content": buffer})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    else:
        # 步数耗尽但未产出 Final Answer → 强制要求模型基于已有 Observation 总结
        # 追加一条 user 消息作为提示，再做一次非流式 LLM 调用
        if messages:
            await trace.log(session_id, step=MAX_STEPS, event="react.forced_summary")

            messages.append({
                "role": "user",
                "content": (
                    "已达到最大推理步数。请基于以上所有 Observation 直接输出 Final Answer，"
                    "不要再调用任何工具。用已获取的数据给出客观分析，末尾附上风险提示。"
                ),
            })
            fallback_buffer = ""
            async for chunk in cloud_chat(messages):
                if chunk["type"] == "text":
                    fallback_buffer += chunk["content"]
                    print(chunk["content"], end="", flush=True)
                elif chunk["type"] == "done":
                    break
            print()
            final_answer = fallback_buffer

    return final_answer
