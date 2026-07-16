import asyncio
import json
import re
import time

from core.dispatch import dispatch_tool
from core.trace import TraceLogger
from core.history_formatter import format_history_dialogue, format_history_assistant_only
from llm_client import cloud_chat, local_chat
from prompts.rewoo import SYSTEM_PROMPT_REWOO_EXTRACT, SYSTEM_PROMPT_REWOO_SYNTHESIS


_CODE_RE = re.compile(r"\b\d{6}\b")
_PER_FUND_TOOLS = {"get_fund_performance", "get_fund_holdings"}


async def _extract_names(user_question: str, history: list[dict], has_context: bool) -> list[str]:
    """LLM 提取需要查询的基金名称，结合历史上下文跳过已覆盖的基金。"""
    history_text = format_history_dialogue(history, header="## 历史对话（用于判断哪些基金数据已覆盖）") if has_context and history else "（无历史对话）"
    prompt = SYSTEM_PROMPT_REWOO_EXTRACT.replace("{history_context}", history_text)

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_question},
    ]

    response = await local_chat(messages, temperature=0.0)
    try:
        data = json.loads(response)
        return data.get("fund_names", [])
    except json.JSONDecodeError:
        return []


async def _resolve_codes(
    user_question: str, tools_needed: list, history: list[dict],
    has_context: bool, trace: TraceLogger, session_id: str,
) -> list[str]:
    """解析基金代码：search_fund 在列表中则 LLM 提取名称后逐个搜索；同时始终从问题中正则提取代码作为兜底。"""
    codes: list[str] = []

    if "search_fund" in tools_needed:
        fund_names = await _extract_names(user_question, history, has_context)

        await trace.log(session_id, step=0, event="rewoo.phase1.done",
                        input={"count": len(fund_names), "names": fund_names})

        for name in fund_names:
            await trace.log(session_id, step=0, event="rewoo.phase2.search",
                            input={"name": name})

            result = await dispatch_tool("search_fund", {"keyword": name})
            m = _CODE_RE.search(result)
            if m:
                codes.append(m.group(0))
            else:
                await trace.log(session_id, step=0, event="rewoo.tool_error",
                                output=f"未找到代码: {name}",
                                input={"tool_name": "search_fund", "name": name})

    # 正则兜底：问题中可能直接给了代码
    direct_codes = _CODE_RE.findall(user_question)
    codes.extend(direct_codes)

    return list(dict.fromkeys(codes))  # 去重保序


async def _call_tool(tool_name: str, params: dict, trace: TraceLogger, session_id: str) -> str:
    """调用单个工具，记录 trace。"""
    t0 = time.perf_counter()
    result = await dispatch_tool(tool_name, params)
    latency = (time.perf_counter() - t0) * 1000

    await trace.log(session_id, step=0, event="rewoo.tool_call",
                    input={"tool_name": tool_name, "params": params},
                    output=result, latency_ms=latency)

    return result


async def _execute_data_tools(
    tools_needed: list, fund_codes: list[str], user_question: str,
    trace: TraceLogger, session_id: str,
) -> dict:
    """并发执行数据工具：per-fund 工具按代码展开，其余工具直接调用。

    工具分类：
    - per-fund 工具（PER_FUND_TOOLS）：按 fund_code 笛卡尔展开，每只基金 × 每个工具
    - 独立工具（如 capital_inflow_in_sectors）：不依赖 fund_code，直接调用
    - search_fund 已在阶段1使用过，阶段2跳过

    asyncio.gather(return_exceptions=True) 确保单个工具失败不会阻塞其他工具。
    """
    tasks: list[tuple[str, dict]] = []

    # per-fund 工具按基金代码展开：N 只基金 × M 个工具 = N*M 次调用
    for code in fund_codes:
        for tool in tools_needed:
            if tool in _PER_FUND_TOOLS:
                tasks.append((tool, {"fund_code": code}))

    # 独立工具（不需要 fund_code，排除已处理的 search_fund）
    for tool in tools_needed:
        if tool not in _PER_FUND_TOOLS and tool != "search_fund":
            params = {"user_query": user_question} if tool == "select_fund" else {}
            tasks.append((tool, params))

    if not tasks:
        return {}

    # 并发执行所有任务，单个工具报错不阻塞其他工具
    coros = [_call_tool(name, params, trace, session_id) for name, params in tasks]
    results = await asyncio.gather(*coros, return_exceptions=True)

    observations: dict[str, str] = {}
    for (tool_name, params), result in zip(tasks, results):
        code = params.get("fund_code", "")
        key = f"{tool_name}:{code}" if code else tool_name
        observations[key] = f"工具调用失败: {result}" if isinstance(result, Exception) else str(result)
    return observations


def _format_observations(observations: dict) -> str:
    """格式化工具查询结果为提示文本。"""
    if not observations:
        return "（无新查询数据）"
    lines = []
    for label, result in observations.items():
        lines.append(f"### {label}")
        lines.append(result)
        lines.append("")
    return "\n".join(lines)


async def _synthesize(
    user_message: list, observations: dict, history: list[dict],
    has_context: bool, trace: TraceLogger, session_id: str,
) -> str:
    """用 cloud_chat 流式生成最终回答（输出直接打印，这是用户可见的回答）。"""
    system_prompt = (
        SYSTEM_PROMPT_REWOO_SYNTHESIS
        .replace("{observations}", _format_observations(observations))
        .replace("{history_context}", format_history_assistant_only(history) if has_context and history else "（无历史数据）")
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(user_message)

    t0 = time.perf_counter()
    print()
    buffer = ""
    llm_usage = None
    gen = cloud_chat(messages)
    try:
        async for chunk in gen:
            if chunk["type"] == "text":
                buffer += chunk["content"]
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "done":
                llm_usage = chunk.get("usage")
                break
    finally:
        await gen.aclose()

    latency = (time.perf_counter() - t0) * 1000
    print()

    await trace.log(session_id, step=0, event="rewoo.phase3.done",
                    latency_ms=latency, tokens=llm_usage,
                    output=buffer[:500])

    return buffer


async def run_rewoo_loop(
    user_message: list, tools_needed: list, history: list[dict],
    has_context: bool, trace: TraceLogger, session_id: str,
) -> str:
    """REWOO 执行器：LLM提取基金名 → 解析代码 → 并发拉数据 → 综合回答。

    Thought/Action/Observation 原文仅写入 trace JSON 日志，
    终端只展示人类可读的进度提示（DEBUG_TRACE=1 时）。
    """
    user_question = user_message[-1]["content"] if user_message else ""

    # ── REWOO 三阶段流水线 ─────────────────────────────────────
    # 阶段1：LLM 提取基金名 → search_fund 搜索代码 → 正则兜底
    # 阶段2：per-fund 工具按代码展开 + 独立工具，全部并发执行
    # 阶段3：将所有 Observation 注入 system prompt，流式生成最终回答

    # ── 阶段1：解析基金名称与代码 ──
    await trace.log(session_id, step=0, event="rewoo.phase1.extract",
                    input={"question": user_question[:200]})

    fund_codes = await _resolve_codes(user_question, tools_needed, history,
                                      has_context, trace, session_id)

    # ── 阶段2：并发获取所有数据（单次 asyncio.gather 全部发出）───
    await trace.log(session_id, step=0, event="rewoo.phase2.fetch",
                    input={"tool_count": len(tools_needed), "fund_count": len(fund_codes)})

    observations = await _execute_data_tools(tools_needed, fund_codes, user_question,
                                             trace, session_id)

    await trace.log(session_id, step=0, event="rewoo.phase2.done",
                    input={"count": len(observations), "keys": list(observations.keys())})

    # ── 阶段3：将所有数据注入 system prompt，流式生成综合分析 ──
    await trace.log(session_id, step=0, event="rewoo.phase3.start")

    return await _synthesize(user_message, observations, history, has_context,
                             trace, session_id)
