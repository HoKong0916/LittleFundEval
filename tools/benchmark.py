"""基准对比工具

自行获取基金净值 + 基准指数行情，统一用固定交易日窗口计算涨跌幅，
核心输出为各窗口超额收益（跑赢/跑输）。

当前策略：固定使用创业板50。
后续扩展：根据基金持仓行业分布，自动匹配最相关的指数。
  例：持仓偏科创 → 科创50(sh000688)，偏大盘 → 沪深300(sh000300)
"""

import asyncio

import akshare as ak
import pandas as pd

from tools.fund_nav_series import fetch_fund_nav_series

INDEX_POOL = {
    "创业板50": "sz399673",
    # "科创50":   "sh000688",
    # "沪深300":  "sh000300",
    # "中证500":  "sh000905",
    # "上证50":   "sh000016",
}

WINDOWS = {"近1月": 22, "近3月": 66, "近6月": 132, "近1年": 252}


async def get_benchmark(fund_code: str) -> dict:
    """获取基金净值 + 基准指数行情 → 统一口径 → 超额收益。"""
    index_name, index_symbol = _pick_benchmark(fund_code)

    fund_df, index_df = await asyncio.gather(
        asyncio.to_thread(fetch_fund_nav_series, fund_code),
        asyncio.to_thread(_fetch_index_series, index_name, index_symbol),
    )

    if index_df is None:
        return {"error": f"{index_name} 无数据"}

    fund_returns = calc_window_returns(fund_df) if fund_df is not None else {}
    index_returns = calc_window_returns(index_df)

    excess = {}
    for window in WINDOWS:
        fr = fund_returns.get(window)
        ir = index_returns.get(window)
        if fr is not None and ir is not None:
            excess[window] = f"{fr - ir:+.2f}%"
        else:
            excess[window] = None

    return {
        "source": "benchmark",
        "基准指数": index_name,
        "基准收益": {k: f"{v:+.2f}%" for k, v in index_returns.items()},
        "基金收益": {k: f"{fr:+.2f}%" if (fr := fund_returns.get(k)) is not None else None for k in WINDOWS},
        "超额收益": excess,
    }


# ── 公用 ──────────────────────────────────────────────────────

def calc_window_returns(df: pd.DataFrame) -> dict[str, float]:
    """统一窗口计算：基金净值和指数行情共用，确保口径一致。"""
    close = df["close"]
    if len(close) < 2:
        return {}
    returns = {}
    for label, days in WINDOWS.items():
        if len(close) > days:
            start_val = close.iloc[-(days + 1)]
            end_val = close.iloc[-1]
            returns[label] = (end_val - start_val) / start_val * 100
    return returns


# ── 内部 ──────────────────────────────────────────────────────

def _fetch_index_series(name: str, symbol: str):
    df = ak.stock_zh_index_daily(symbol=symbol)
    if df is None or df.empty:
        raise ValueError(f"{name} 无数据")
    return df.sort_values("date")


def _pick_benchmark(fund_code: str) -> tuple[str, str]:
    return "创业板50", INDEX_POOL["创业板50"]
