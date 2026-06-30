"""基金净值数据工具

通过丹橘API + fundgz + akshare 获取基金全面指标，纯透传无 LLM。
丹橘 + fundgz 异步并发请求，akshare 投线程池。
"""
import asyncio
import json
import re

import akshare as ak
import httpx
import pandas as pd

if not hasattr(pd.DataFrame, "map"):
    pd.DataFrame.map = lambda self, func, **_: self.applymap(func)


async def get_fund_nav(fund_code: str) -> dict:
    """获取基金全面指标。丹橘 + fundgz + akshare 三路并发。"""
    result = {"source": "fund_nav", "fund_code": fund_code}
    errors = []

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as client:
        d_task = _fetch_danjuan(result, fund_code, client)
        g_task = _fetch_fundgz(result, fund_code, client)
        a_task = asyncio.to_thread(_fetch_analysis, result, fund_code)

        gathered = await asyncio.gather(d_task, g_task, a_task, return_exceptions=True)
        for name, r in zip(("丹橘", "fundgz", "analysis"), gathered):
            if isinstance(r, Exception):
                errors.append(f"{name}: {r}")

    if errors and not any(k in result for k in ("最新净值", "近1月收益", "近1年收益")):
        return {"error": "; ".join(errors)}
    return result



# --- 各数据源获取函数 ---

async def _fetch_danjuan(result: dict, fund_code: str, client: httpx.AsyncClient) -> None:
    r = await client.get(f"https://danjuanfunds.com/djapi/fund/{fund_code}", timeout=10)
    r.raise_for_status()
    data = r.json()
    derived = (data.get("data") or {}).get("fund_derived") or {}

    def _pct(val, default=None):
        if val is None:
            return default
        try:
            return f"{float(val):+.2f}%"
        except (ValueError, TypeError):
            return default

    data_block = data.get("data") or {}
    fund_name = data_block.get("fd_name") or data_block.get("name", "")
    if fund_name:
        result["基金名称"] = fund_name

    result.update({
        "基金规模": data_block.get("totshare"),
        "近1月收益": _pct(derived.get("nav_grl1m")),
        "近3月收益": _pct(derived.get("nav_grl3m")),
        "近6月收益": _pct(derived.get("nav_grl6m")),
        "近1年收益": _pct(derived.get("nav_grl1y")),
        "近1月排名": derived.get("srank_l1m"),
        "近3月排名": derived.get("srank_l3m"),
        "近6月排名": derived.get("srank_l6m"),
        "近1年排名": derived.get("srank_l1y"),
    })


async def _fetch_fundgz(result: dict, fund_code: str, client: httpx.AsyncClient) -> None:
    r = await client.get(f"https://fundgz.1234567.com.cn/js/{fund_code}.js", timeout=10)
    r.raise_for_status()
    match = re.search(r"jsonpgz\((\{.*\})\)", r.text)
    if not match:
        return
    gz = json.loads(match.group(1))
    result["最新净值日期"] = gz.get("jzrq")
    result["上一交易日净值"] = float(gz["dwjz"]) if gz.get("dwjz") else None
    result["当天交易日估算值"] = float(gz["gsz"]) if gz.get("gsz") else None
    gszzl = gz.get("gszzl")
    result["估算增长率"] = f"{float(gszzl):+.2f}%" if gszzl else None


def _fetch_analysis(result: dict, fund_code: str) -> None:
    """同步函数，由 asyncio.to_thread 调用"""
    df = ak.fund_individual_analysis_xq(symbol=fund_code)
    row = df[df.iloc[:, 0].str.contains("近1年")]
    if row.empty:
        return
    r = row.iloc[0]
    result["最大回撤"] = f"-{float(r.iloc[5]):.2f}%"
    result["年化波动率"] = f"{float(r.iloc[3]):.2f}%"
    result["夏普比率"] = float(r.iloc[4])
