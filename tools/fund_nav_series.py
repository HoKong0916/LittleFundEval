"""基金净值序列获取（内部工具，不注册到 ToolRegistry）。

提供获取基金历史日频净值的统一入口，多个数据源 fallback。
benchmark 等工具内部引用，不对外暴露为独立工具。
"""

import akshare as ak


def fetch_fund_nav_series(fund_code: str):
    """获取基金历史净值序列（日频），返回至少含 'close' 和 'date' 列的 DataFrame。"""
    for fn in (_try_etf_hist, _try_fund_nav_em, _try_fund_info):
        df = fn(fund_code)
        if df is not None:
            return df
    return None


def _try_etf_hist(fund_code: str):
    try:
        df = ak.fund_etf_hist_em(symbol=fund_code, period="daily")
        if df is not None and not df.empty and "收盘价" in df.columns:
            df = df.rename(columns={"收盘价": "close"})
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            return df
    except Exception:
        pass
    return None


def _try_fund_nav_em(fund_code: str):
    try:
        df = ak.fund_nav_em(symbol=fund_code)
        if df is not None and not df.empty and "单位净值" in df.columns:
            return df.rename(columns={"单位净值": "close", "净值日期": "date"})
    except Exception:
        pass
    return None


def _try_fund_info(fund_code: str):
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is not None and not df.empty and "单位净值" in df.columns:
            df = df.rename(columns={"单位净值": "close"})
            if "净值日期" in df.columns:
                df = df.rename(columns={"净值日期": "date"})
            return df
    except Exception:
        pass
    return None
