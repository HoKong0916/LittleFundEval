import asyncio
import json
import re

from core.dispatch import dispatch_tool
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

    response = local_chat(messages, temperature=0.0)
    try:
        data = json.loads(response)
        return data.get("fund_names", [])
    except json.JSONDecodeError:
        print(f"⚠️ 基金名称提取失败: {response[:200]}")
        return []


async def _resolve_codes(user_question: str, tools_needed: list, history: list[dict], has_context: bool) -> list[str]:
    """解析基金代码：search_fund 在列表中则 LLM 提取名称后逐个搜索，否则正则提取。"""
    codes: list[str] = []

    if "search_fund" in tools_needed:
        fund_names = await _extract_names(user_question, history, has_context)
        for name in fund_names:
            print(f"🔍 搜索基金: {name}")
            result = await dispatch_tool("search_fund", {"keyword": name})
            print(f"📋 {result}")
            m = _CODE_RE.search(result)
            if m:
                codes.append(m.group(0))
            else:
                print(f"⚠️ 未找到代码: {name}")

    # 正则兜底：问题中可能直接给了代码
    direct_codes = _CODE_RE.findall(user_question)
    codes.extend(direct_codes)

    return list(dict.fromkeys(codes))  # 去重保序


async def _call_tool(tool_name: str, params: dict) -> str:
    """调用单个工具并打印进度。"""
    params_str = ", ".join(f'{k}="{v}"' for k, v in params.items())
    print(f"⏳ {tool_name}({params_str})")
    result = await dispatch_tool(tool_name, params)
    print(f"📋 {result}")
    return result


async def _execute_data_tools(tools_needed: list, fund_codes: list[str], user_question: str) -> dict:
    """并发执行数据工具：per-fund 工具按代码展开，其余工具直接调用。"""
    tasks: list[tuple[str, dict]] = []  # [(tool_name, params)]

    # per-fund 工具：每只基金 × 每个工具
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

    coros = [_call_tool(name, params) for name, params in tasks]
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


async def _synthesize(user_message: list, observations: dict, history: list[dict], has_context: bool) -> str:
    """用 cloud_chat 流式生成最终回答。"""
    system_prompt = (
        SYSTEM_PROMPT_REWOO_SYNTHESIS
        .replace("{observations}", _format_observations(observations))
        .replace("{history_context}", format_history_assistant_only(history) if has_context and history else "（无历史数据）")
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(user_message)

    print()
    buffer = ""
    gen = cloud_chat(messages)
    try:
        async for chunk in gen:
            if chunk["type"] == "text":
                buffer += chunk["content"]
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "done":
                break
    finally:
        await gen.aclose()

    print()
    return buffer


async def run_rewoo_loop(user_message: list, tools_needed: list, history: list[dict], has_context: bool) -> str:
    """REWOO 执行器：LLM提取基金名 → 解析代码 → 并发拉数据 → 综合回答。"""
    user_question = user_message[-1]["content"] if user_message else ""

    print(f"\n{'─' * 50}")
    print("🧩 阶段1：解析基金代码")

    fund_codes = await _resolve_codes(user_question, tools_needed, history, has_context)

    print(f"\n{'─' * 50}")
    print("🧩 阶段2：并发获取数据")

    observations = await _execute_data_tools(tools_needed, fund_codes, user_question)

    print(f"\n{'─' * 50}")
    print("🧩 阶段3：综合分析")

    return await _synthesize(user_message, observations, history, has_context)
