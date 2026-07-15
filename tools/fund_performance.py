"""基金基本面工具 —— 从收益、风险、当天三个维度评估一只基金。

数据源：丹橘(收益/排名/基本信息) + fundgz(实时估算净值) + akshare(波动率/夏普/回撤-近1年)，三路并发。
"""

import asyncio
import json
import re

import akshare as ak
import httpx
import pandas as pd

# akshare 依赖 pandas 2.1+ 的 DataFrame.map，低版本做兼容
if not hasattr(pd.DataFrame, "map"):
    pd.DataFrame.map = lambda self, func, **_: self.applymap(func)


async def get_fund_performance(fund_code: str) -> str:
    """获取基金基本面：收益状况 + 风险状况 + 当天状况，返回 LLM 可直接使用的文本。"""
    result: dict = {"基金代码": fund_code}
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

    return _format(result, errors)


# ═══════════════════════════════════════════════════════════════════
# 数据源
# ═══════════════════════════════════════════════════════════════════


async def _fetch_danjuan(result: dict, fund_code: str, client: httpx.AsyncClient) -> None:
    """从丹橘API提取收益、排名、规模、基本信息。"""
    r = await client.get(f"https://danjuanfunds.com/djapi/fund/{fund_code}", timeout=10)
    r.raise_for_status()
    data = r.json()
    data_block = data.get("data") or {}
    derived = data_block.get("fund_derived") or {}

    # ── 基本信息 ──
    result["基金名称"] = data_block.get("fd_name") or data_block.get("name", "")
    result["基金经理"] = data_block.get("manager_name", "")
    result["成立时间"] = _ts_to_date(data_block.get("found_date"))

    # 基金类型：从 op_fund.fund_tags 提取
    tags = (data_block.get("op_fund") or {}).get("fund_tags") or []
    type_desc = next((t["name"] for t in tags if t.get("category") == "1"), "")
    risk_desc = next((t["name"] for t in tags if t.get("category") == "9"), "")
    result["基金类型"] = f"{type_desc} | {risk_desc}" if type_desc and risk_desc else (type_desc or risk_desc or "")

    def _pct(val):
        if val is None:
            return None
        try:
            return f"{float(val):+.2f}%"
        except (ValueError, TypeError):
            return None

    # ── 收益状况 ──
    result["日涨跌"] = _pct(derived.get("nav_grtd"))
    result["近1月收益"] = _pct(derived.get("nav_grl1m"))
    result["近3月收益"] = _pct(derived.get("nav_grl3m"))
    result["近6月收益"] = _pct(derived.get("nav_grl6m"))
    result["近1年收益"] = _pct(derived.get("nav_grl1y"))

    result["近1月排名"] = derived.get("srank_l1m")
    result["近3月排名"] = derived.get("srank_l3m")
    result["近6月排名"] = derived.get("srank_l6m")
    result["近1年排名"] = derived.get("srank_l1y")

    # ── 当天状况 ──
    result["最新净值"] = derived.get("unit_nav")
    result["净值日期"] = derived.get("end_date")
    result["基金规模"] = data_block.get("totshare")


def _ts_to_date(ts) -> str:
    """毫秒时间戳 → YYYY-MM-DD"""
    if not ts:
        return ""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


async def _fetch_fundgz(result: dict, fund_code: str, client: httpx.AsyncClient) -> None:
    """从天天基金 fundgz 接口获取实时估算净值与涨幅。"""
    r = await client.get(f"https://fundgz.1234567.com.cn/js/{fund_code}.js", timeout=10)
    r.raise_for_status()
    match = re.search(r"jsonpgz\((\{.*\})\)", r.text)
    if not match:
        return
    gz = json.loads(match.group(1))
    result["估值日期"] = gz.get("gztime", "")[:10]
    result["估值时间"] = gz.get("gztime", "")[11:16] if len(gz.get("gztime", "")) > 11 else ""
    try:
        result["估算净值"] = float(gz["gsz"]) if gz.get("gsz") else None
    except (ValueError, TypeError):
        pass
    gszzl = gz.get("gszzl")
    result["估算涨幅"] = f"{float(gszzl):+.2f}%" if gszzl else None


def _fetch_risk(result: dict, fund_code: str) -> None:
    """通过 akshare 获取近1年的风险指标。"""
    try:
        df = ak.fund_individual_analysis_xq(symbol=fund_code)
    except Exception:
        return
    # 列: 周期, 较同类风险收益比, 较同类抗风险波动, 年化波动率, 年化夏普比率, 最大回撤
    row = df[df.iloc[:, 0].str.contains("近1年")]
    if row.empty:
        return
    r = row.iloc[0]

    def _level(v: float) -> str:
        if v < 30:
            label = "弱"
        elif v < 60:
            label = "中"
        else:
            label = "强"
        return f"{label}(优于{v:.0f}%同类)"

    try:
        result["较同类风险收益比"] = _level(float(r.iloc[1]))
    except (ValueError, IndexError):
        pass
    try:
        result["较同类抗风险波动"] = _level(float(r.iloc[2]))
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
    try:
        result["最大回撤"] = f"-{float(r.iloc[5]):.2f}%"
    except (ValueError, IndexError):
        pass


# ═══════════════════════════════════════════════════════════════════
# 格式化
# ═══════════════════════════════════════════════════════════════════


def _format(d: dict, errors: list[str]) -> str:
    lines = []

    # ── 头部：基本信息 ──
    code = d.get("基金代码", "")
    name = d.get("基金名称", "未知基金")
    parts = [f"{name}({code})" if code else name]
    manager = d.get("基金经理", "")
    if manager:
        parts.append(f"基金经理: {manager}")
    fund_type = d.get("基金类型", "")
    if fund_type:
        parts.append(fund_type)
    found = d.get("成立时间", "")
    if found:
        parts.append(f"成立: {found}")
    scale = d.get("基金规模", "")
    if scale:
        parts.append(f"规模: {scale}")
    lines.append(" | ".join(parts))

    # ── 收益状况 ──
    lines.append("")
    lines.append("━━━ 收益状况 ━━━")
    returns_rows = [
        ("近1月", "近1月收益", "近1月排名"),
        ("近3月", "近3月收益", "近3月排名"),
        ("近6月", "近6月收益", "近6月排名"),
        ("近1年", "近1年收益", "近1年排名"),
    ]
    for label, ret_key, rank_key in returns_rows:
        ret = d.get(ret_key)
        rank = d.get(rank_key, "")
        if ret:
            line = f"  {label:　<6} {ret}"
            if rank:
                line += f"  同类排名: {rank}"
            lines.append(line)

    # ── 风险状况 ──
    lines.append("")
    lines.append("━━━ 风险状况 ━━━")
    risk_items = [
        ("最大回撤",          "最大回撤",          "近1年"),
        ("年化波动率",        "年化波动率",        "近1年"),
        ("夏普比率",          "夏普比率",          "近1年"),
        ("较同类风险收益比",  "较同类风险收益比",  ""),
        ("较同类抗风险波动",  "较同类抗风险波动",  ""),
    ]
    for label, key, period in risk_items:
        v = d.get(key)
        if v is not None and v != "":
            line = f"  {label:　<10} {v}"
            if period:
                line += f"  ({period})"
            lines.append(line)

    # ── 当天状况 ──
    lines.append("")
    lines.append("━━━ 当天状况 ━━━")
    nav_date = d.get("净值日期", "")
    nav = d.get("最新净值")
    if nav is not None:
        lines.append(f"  最新净值({nav_date}): {nav}")
    day_chg = d.get("日涨跌")
    if day_chg:
        lines.append(f"  日涨跌: {day_chg}")

    est_nav = d.get("估算净值")
    est_chg = d.get("估算涨幅")
    est_date = d.get("估值日期", "")
    est_time = d.get("估值时间", "")
    if est_nav is not None:
        line = f"  估算净值({est_date} {est_time}): {est_nav:.4f}"
        if est_chg:
            line += f"  {est_chg}"
        lines.append(line)

    # ── 数据源（有错误时显示）──
    if errors:
        lines.append("")
        lines.append(f"⚠️ 部分数据源异常: {'; '.join(errors)}")

    return "\n".join(lines)
