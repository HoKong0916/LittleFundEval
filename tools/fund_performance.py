"""基金业绩与风险指标工具 —— 合并 get_fund_performance + get_fund_risk。

数据源：丹橘(业绩+排名) + fundgz(实时净值) + akshare(风险指标)，三路并发。
"""

import asyncio
import json
import re

import akshare as ak
import httpx


async def get_fund_performance(fund_code: str) -> str:
    """获取基金阶段收益 + 风险指标，返回 LLM 可直接使用的文本。"""
    result: dict = {}
    errors: list[str] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    ) as client:
        d_task = _fetch_danjuan(result, fund_code, client)
        g_task = _fetch_fundgz(result, fund_code, client)
        a_task = asyncio.to_thread(_fetch_risk, result, fund_code)

        gathered = await asyncio.gather(d_task, g_task, a_task, return_exceptions=True)
        for name, r in zip(("丹橘", "fundgz", "akshare"), gathered):
            if isinstance(r, Exception):
                errors.append(f"{name}: {r}")

    if errors and not result:
        return f"错误: {'; '.join(errors)}"

    return _format(result)


# ── 数据源 ──────────────────────────────────────────────────────


async def _fetch_danjuan(result: dict, fund_code: str, client: httpx.AsyncClient) -> None:
    r = await client.get(f"https://danjuanfunds.com/djapi/fund/{fund_code}", timeout=10)
    r.raise_for_status()
    data = r.json()
    derived = (data.get("data") or {}).get("fund_derived") or {}
    data_block = data.get("data") or {}

    fund_name = data_block.get("fd_name") or data_block.get("name", "")
    if fund_name:
        result["基金名称"] = fund_name

    result["基金规模"] = data_block.get("totshare")

    def _pct(val):
        if val is None:
            return None
        try:
            return f"{float(val):+.2f}%"
        except (ValueError, TypeError):
            return None

    result["近1月收益"] = _pct(derived.get("nav_grl1m"))
    result["近3月收益"] = _pct(derived.get("nav_grl3m"))
    result["近6月收益"] = _pct(derived.get("nav_grl6m"))
    result["近1年收益"] = _pct(derived.get("nav_grl1y"))
    result["近1月排名"] = derived.get("srank_l1m")
    result["近3月排名"] = derived.get("srank_l3m")
    result["近6月排名"] = derived.get("srank_l6m")
    result["近1年排名"] = derived.get("srank_l1y")


async def _fetch_fundgz(result: dict, fund_code: str, client: httpx.AsyncClient) -> None:
    r = await client.get(f"https://fundgz.1234567.com.cn/js/{fund_code}.js", timeout=10)
    r.raise_for_status()
    match = re.search(r"jsonpgz\((\{.*\})\)", r.text)
    if not match:
        return
    gz = json.loads(match.group(1))
    result["最新净值日期"] = gz.get("jzrq")
    result["上一交易日净值"] = float(gz["dwjz"]) if gz.get("dwjz") else None
    result["当天估算值"] = float(gz["gsz"]) if gz.get("gsz") else None
    gszzl = gz.get("gszzl")
    result["估算增长率"] = f"{float(gszzl):+.2f}%" if gszzl else None


def _fetch_risk(result: dict, fund_code: str) -> None:
    """同步函数，由 asyncio.to_thread 调用"""
    try:
        df = ak.fund_individual_analysis_xq(symbol=fund_code)
    except Exception:
        return
    row = df[df.iloc[:, 0].str.contains("近1年")]
    if row.empty:
        return
    r = row.iloc[0]
    try:
        result["最大回撤"] = f"-{float(r.iloc[5]):.2f}%"
    except (ValueError, IndexError):
        pass
    try:
        result["年化波动率"] = f"{float(r.iloc[3]):.2f}%"
    except (ValueError, IndexError):
        pass
    try:
        result["夏普比率"] = float(r.iloc[4])
    except (ValueError, IndexError):
        pass


# ── 格式化 ──────────────────────────────────────────────────────


def _format(d: dict) -> str:
    lines = []

    name = d.get("基金名称", "")
    scale = d.get("基金规模", "")
    if name:
        header = f"{name}"
        if scale:
            header += f"，规模 {scale}"
        lines.append(header)

    # 净值
    nav_date = d.get("最新净值日期", "")
    nav = d.get("上一交易日净值")
    est = d.get("当天估算值")
    est_chg = d.get("估算增长率")
    if nav is not None:
        nav_line = f"净值({nav_date}): {nav:.4f}"
        if est is not None:
            nav_line += f" 估算: {est:.4f}"
            if est_chg:
                nav_line += f" ({est_chg})"
        lines.append(nav_line)

    # 阶段收益
    returns = []
    for period in ("近1月", "近3月", "近6月", "近1年"):
        v = d.get(f"{period}收益")
        if v:
            returns.append(f"{period} {v}")
    if returns:
        lines.append("阶段收益: " + " | ".join(returns))

    # 排名
    rankings = []
    for period in ("近1月", "近3月", "近6月", "近1年"):
        v = d.get(f"{period}排名")
        if v:
            rankings.append(f"{period} {v}")
    if rankings:
        lines.append("同类排名: " + " | ".join(rankings))

    # 风险
    risk_parts = []
    for key, label in (("最大回撤", "最大回撤"), ("年化波动率", "年化波动率"), ("夏普比率", "夏普比率")):
        v = d.get(key)
        if v is not None:
            risk_parts.append(f"{label} {v}")
    if risk_parts:
        lines.append("风险指标: " + " | ".join(risk_parts))

    return "\n".join(lines)
