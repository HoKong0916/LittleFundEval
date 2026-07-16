import asyncio
import json
import re

import httpx
from llm_client import local_chat
from prompts.capital_inflow import SYSTEM_PROMPT_CAPITAL_INFLOW


_PERIOD_LABEL = {
    "FLOW": "今日",
    "FLOW_W": "近1周",
    "FLOW_M": "近1月",
    "FLOW_Q": "近3月",
}


def _format_flow(val: float) -> str:
    """资金流量格式化为亿，正数带 + 号，负数 - 号。"""
    yi = val / 1e8
    sign = "+" if yi > 0 else "-"
    return f"{sign}{yi:.2f}亿"


async def _fetch_period(client: httpx.AsyncClient, st: str) -> tuple[str, list[dict]] | None:
    """抓取单个时间段（今日/近1周/近1月/近3月）的板块资金流向数据。

    错误全吞并返回 None —— 四个时间段并发抓取，单个失败不影响其余。
    """
    url = "https://api.fund.eastmoney.com/ztjj/GetZTJJListNew"
    params = {"tt": "0", "dt": "zjlr", "st": st}

    try:
        raw = (await client.get(url=url, params=params)).text
    except Exception:
        return None

    # jQuery callback 解包: jQuery123({...}) → {...}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        payload = json.loads(m.group())
    except json.JSONDecodeError:
        return None

    sector_list = payload.get("Data") or []
    return st, [
        {"名称": s["INDEXNAME"], "代码": s["INDEXCODE"], "流量": s[st]}
        for s in sector_list
    ]


async def _match_sectors(user_sectors: list[str], available_names: list[str]) -> list[str]:
    """用本地 LLM 将用户输入的板块名模糊匹配到 API 实际返回的板块名。

    返回与 user_sectors 一一对应的匹配结果，匹配不到的为空字符串。
    """
    board_names = "\n".join(f"- {n}" for n in available_names)
    system_prompt = SYSTEM_PROMPT_CAPITAL_INFLOW.replace("{board_names}", board_names)
    user_text = "、".join(user_sectors)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"用户想查询的板块: {user_text}"},
    ]

    try:
        response = await local_chat(messages, temperature=0.0)
        result = json.loads(response)
        matched = result.get("matched", [])
        # 保证长度一致
        if len(matched) != len(user_sectors):
            return user_sectors  # 降级：原样返回
        return matched
    except Exception:
        return user_sectors  # 降级：原样返回


async def capital_inflow_in_sectors(sectors: list[str] | None = None) -> str:
    """获取各板块资金流向（今日 / 近1周 / 近1月 / 近3月），返回格式化文本。

    Args:
        sectors: 可选，指定板块名称列表。传入后按板块维度展示每个板块在四个时间段的资金流向；
                 不传则展示各时间段 TOP5 流入/流出。
    """

    period_data: dict[str, list[dict]] = {}

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://fund.eastmoney.com/",
        },
        timeout=30,
    ) as client:

        # 四个时间段并发抓取，return_exceptions=True 防止单段异常取消其余请求
        results = await asyncio.gather(
            _fetch_period(client, "FLOW"),
            _fetch_period(client, "FLOW_W"),
            _fetch_period(client, "FLOW_M"),
            _fetch_period(client, "FLOW_Q"),
            return_exceptions=True,
        )
        for result in results:
            if result is not None:
                st, data = result
                period_data[st] = data

    if not period_data:
        return "错误: 未获取到任何板块资金流向数据"

    lines = ["━━━ 板块资金流向 ━━━", ""]

    if sectors:
        # ── 收集所有实际板块名，LLM 模糊匹配 ──
        all_sector_names: list[str] = []
        for sector_list in period_data.values():
            for s in sector_list:
                name = s["名称"]
                if name not in all_sector_names:
                    all_sector_names.append(name)

        matched_sectors = await _match_sectors(sectors, all_sector_names)

        # ── 指定板块模式：按板块维度展示 ──
        for user_input, matched_name in zip(sectors, matched_sectors):
            lines.append(f"【{user_input}】")
            if not matched_name:
                lines.append("  未匹配到对应板块")
                lines.append("")
                continue
            for st, label in _PERIOD_LABEL.items():
                sector_list = period_data.get(st)
                if not sector_list:
                    continue
                hit = next((s for s in sector_list if s["名称"] == matched_name), None)
                if hit:
                    lines.append(f"  {label:　<6} {_format_flow(hit['流量'])}")
                else:
                    lines.append(f"  {label:　<6} 无数据")
            lines.append("")
    else:
        # ── 默认模式：各时间段 TOP5 流入/流出 ──
        TOP_N = 5
        for st, label in _PERIOD_LABEL.items():
            sector_list = period_data.get(st)
            if not sector_list:
                continue

            inflow = [s for s in sector_list if s["流量"] > 0][:TOP_N]
            outflow = [s for s in sector_list if s["流量"] < 0][-TOP_N:]
            outflow.reverse()

            lines.append(f"【{label}】")

            if inflow:
                lines.append("  流入 TOP5:")
                for s in inflow:
                    lines.append(f"    {s['名称']:　<10} {_format_flow(s['流量'])}")

            if outflow:
                lines.append("  流出 TOP5:")
                for s in outflow:
                    lines.append(f"    {s['名称']:　<10} {_format_flow(s['流量'])}")

            lines.append("")

    return "\n".join(lines)
