"""基金持仓数据工具

通过 akshare 获取基金最新季度持仓明细，
纯代码计算关键指标，无 LLM 依赖。
"""

import asyncio
import re

import akshare as ak
import pandas as pd
from datetime import datetime


def _get_report_year() -> int:
    """推算最新可用的财报年份：若当前月份 ≤3，则上一年年报可能尚未披露"""
    return datetime.now().year - 1 if datetime.now().month <= 3 else datetime.now().year


def _find_column(cols, *keywords) -> str | None:
    """在列名列表中查找包含所有关键词的第一个列名"""
    for col in cols:
        if all(kw in col for kw in keywords):
            return col
    return None


def _to_float(val) -> float:
    """将 akshare 返回的数值转为 float，处理字符串、百分号、逗号"""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.replace("%", "").replace(",", "").strip()
        try:
            return float(val)
        except ValueError:
            return 0.0
    return 0.0


def _quarter_key(q: str) -> tuple:
    """从 '2026年1季度股票投资明细' 提取 (年份, 季度号) 用于排序"""
    m = re.search(r"(\d{4})年(\d)季度", q)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _fmt_quarter(q: str) -> str:
    """格式化季度标签：'2026年1季度...' → '2026Q1'"""
    m = re.search(r"(\d{4})年(\d)季度", q)
    return f"{m.group(1)}Q{m.group(2)}" if m else q


async def get_fund_holdings(fund_code: str, top_n: int = 10) -> dict:
    """获取基金最新两个季度持仓，计算前 N 大重仓股、市值占比和变化"""
    return await asyncio.to_thread(_get_fund_holdings_sync, fund_code, top_n)


def _get_fund_holdings_sync(fund_code: str, top_n: int = 10) -> dict:
    year = _get_report_year()

    def _fetch_year(target_year: int):
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=str(target_year))
            if df is not None and not df.empty:
                return df
        except Exception:
            pass
        return None

    df_current = _fetch_year(year)
    df_prev = _fetch_year(year - 1)

    all_dfs = []
    if df_current is not None:
        all_dfs.append(df_current)
    if df_prev is not None:
        all_dfs.append(df_prev)

    if not all_dfs:
        return {"error": f"基金 {fund_code} 暂无持仓数据"}

    combined = pd.concat(all_dfs, ignore_index=True)
    quarter_col = combined.columns[-1]
    quarters = sorted(combined[quarter_col].unique(), key=_quarter_key)

    if len(quarters) < 1:
        return {"error": f"基金 {fund_code} 暂无持仓数据"}

    latest_q = quarters[-1]
    prev_q = quarters[-2] if len(quarters) >= 2 else None

    cols = combined.columns.tolist()
    ratio_col = _find_column(cols, "占净值", "比例") or _find_column(cols, "净值", "比例") or _find_column(cols, "比例")
    market_col = _find_column(cols, "持仓", "市值") or _find_column(cols, "市值")
    code_col = _find_column(cols, "股票", "代码") or _find_column(cols, "代码")
    name_col = _find_column(cols, "股票", "名称") or _find_column(cols, "名称")

    if not ratio_col:
        return {"error": f"基金 {fund_code} 持仓数据中未找到占净值比例列，实际列: {cols}"}

    def _top_n_from(df_q: pd.DataFrame, n: int):
        """按占净值比例降序取 top N"""
        df_sorted = df_q.copy()
        df_sorted["_ratio_num"] = df_sorted[ratio_col].apply(_to_float)
        return df_sorted.sort_values("_ratio_num", ascending=False).head(n)

    # 当前季度
    df_latest = combined[combined[quarter_col] == latest_q]
    top_latest = _top_n_from(df_latest, top_n)

    top_stocks = [str(c) for c in (top_latest[code_col].tolist() if code_col else [])]
    top_ratio = top_latest["_ratio_num"].sum()

    # 上一季度
    top_prev = None
    if prev_q is not None:
        df_prev_q = combined[combined[quarter_col] == prev_q]
        top_prev = _top_n_from(df_prev_q, top_n)

    # 持仓变化：新增 / 剔除
    if top_prev is not None and code_col and name_col:
        prev_codes = set(top_prev[code_col].tolist())
        curr_codes = set(top_latest[code_col].tolist())
        new_codes = curr_codes - prev_codes
        removed_codes = prev_codes - curr_codes

        new_names = top_latest[top_latest[code_col].isin(new_codes)][name_col].tolist()
        removed_names = top_prev[top_prev[code_col].isin(removed_codes)][name_col].tolist()

        parts = [f"与上一期({_fmt_quarter(prev_q)})相比"]
        if new_names:
            parts.append(f"新增{len(new_codes)}只({', '.join(new_names)})")
        if removed_names:
            if new_names:
                parts.append("，")
            parts.append(f"剔除{len(removed_codes)}只({', '.join(removed_names)})")
        if not new_codes and not removed_codes:
            parts[0] = "与上一期相比，前十大重仓股无变化"
        change_desc = "".join(parts)
    else:
        change_desc = "无上一期数据可比"

    def _build_detail(df_detail: pd.DataFrame) -> list[dict]:
        records = []
        for _, row in df_detail.iterrows():
            rec: dict = {}
            if code_col:
                rec["股票代码"] = str(row[code_col])
            if name_col:
                rec["股票名称"] = str(row[name_col])
            rec["占净值比例"] = f"{row['_ratio_num']:.2f}%"
            if market_col:
                rec["持仓市值"] = _to_float(row[market_col])
            records.append(rec)
        return records

    result: dict = {
        "source": "fund_holdings",
        "fund_code": fund_code,
        "当前报告期": _fmt_quarter(latest_q),
        "上一报告期": _fmt_quarter(prev_q) if prev_q else None,
        "前十大重仓股": top_stocks,
        "十大重仓市值占比": f"{top_ratio:.2f}%",
        "持仓变化描述": change_desc,
        "当前季度持仓明细": _build_detail(top_latest),
    }
    if top_prev is not None:
        result["上一季度持仓明细"] = _build_detail(top_prev)

    return result

