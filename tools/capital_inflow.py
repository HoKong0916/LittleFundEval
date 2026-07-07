import json
import re

import httpx


_PERIOD_LABEL = {
    "FLOW": "今日",
    "FLOW_W": "近1周",
    "FLOW_M": "近1月",
    # "FLOW_Q": "近3月",
}


def _format_flow(val: float) -> str:
    """资金流量格式化为亿，正数红色标记，负数绿色标记。"""
    yi = val / 1e8
    sign = "+" if yi > 0 else ""
    return f"{sign}{yi:.2f}亿"


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
        }
    ) as client:
        for st in ("FLOW", "FLOW_W", "FLOW_M", "FLOW_Q"):
            url = "https://api.fund.eastmoney.com/ztjj/GetZTJJListNew"
            params = {"tt": "0", "dt": "zjlr", "st": st}

            try:
                raw = (await client.get(url=url, params=params)).text
            except Exception:
                continue

            # jQuery callback 解包: jQuery123({...}) → {...}
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                continue
            try:
                payload = json.loads(m.group())
            except json.JSONDecodeError:
                continue

            sector_list = payload.get("Data") or []
            # API 已按流入金额降序排列（正值在前，负值在后）
            period_data[st] = [
                {"名称": s["INDEXNAME"], "代码": s["INDEXCODE"], "流量": s[st]}
                for s in sector_list
            ]

    if not period_data:
        return "错误: 未获取到任何板块资金流向数据"

    lines = ["━━━ 板块资金流向 ━━━", ""]

    if sectors:
        # ── 指定板块模式：按板块维度展示 ──
        for name in sectors:
            lines.append(f"【{name}】")
            for st, label in _PERIOD_LABEL.items():
                sector_list = period_data.get(st)
                if not sector_list:
                    continue
                hit = next((s for s in sector_list if s["名称"] == name), None)
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
