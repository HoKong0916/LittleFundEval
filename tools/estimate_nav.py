"""基金盘中净值估算 —— 重仓股涨跌×持仓比例 + 板块代理涨幅×非重仓仓位。

比 fundgz 接口简单估算更精细，逐只计算持仓贡献。

内部两层：_calc_nav_estimate（结构化结果）→ estimate_fund_nav（格式化文本）。
其他工具可直接调 _calc_nav_estimate 避免文本往返。

公式：
    估算涨幅 ≈ 重仓股贡献 + 非重仓板块代理贡献 + 非股票部分（≈0）
    重仓股贡献   = Σ(占净资产比% × 当日涨跌幅%) / 100
    非重仓股贡献 = (股票仓位% - 前十合计%) × 板块代理涨幅% / 100

适用：股票仓位≥30%、非 QDII/跨境、非宽基指数、前十覆盖股票仓位≥60%。
时效：季报发布 ≤1月高可信，1-2月可能调仓，>2月参考价值下降。
"""

import asyncio
import json
import re
from datetime import datetime, timedelta

import akshare as ak
import httpx

from tools.fund_holding import _fetch_holdings_data


# ── 资产配置（含兜底推断）─────────────────────────────────────────


def _infer_asset_allocation_by_type(fund_code: str) -> dict:
    """akshare 失败时的兜底：ETF/LOF(15/16/51/52) 按 93% 股票仓位，其余按混合型 70%。"""
    prefix = fund_code[:2] if len(fund_code) >= 2 else ""
    if prefix in ("15", "16", "51", "52"):
        return {"股票": 93.0, "债券": 2.0, "现金": 5.0, "_source": "inferred_etf"}
    return {"股票": 70.0, "债券": 15.0, "现金": 15.0, "_source": "inferred_mixed"}


def portfolio_asset_allocation(fund_code: str, date: str) -> dict[str, str] | None:
    """akshare 拉取最新季报资产配置，返回 {"资产类型": "仓位占比%"}。"""
    date = date.replace("-", "")
    fund_individual_detail_hold_xq_df = ak.fund_individual_detail_hold_xq(symbol=fund_code, date=date)
    result = {k: f"{v}%" for k, v in zip(fund_individual_detail_hold_xq_df['资产类型'], fund_individual_detail_hold_xq_df['仓位占比'])}
    return result


def _get_asset_allocation(fund_code: str, date: str) -> dict:
    """优先 akshare，失败走前缀推断。返回 {股票/债券/现金: float, _source: str}。"""
    try:
        alloc = portfolio_asset_allocation(fund_code, date)
    except Exception:
        alloc = None
    if alloc:
        parsed: dict = {}
        for k, v in alloc.items():
            try:
                parsed[k] = float(str(v).replace("%", ""))
            except (ValueError, TypeError):
                parsed[k] = 0.0
        parsed["_source"] = "akshare"
        return parsed
    return _infer_asset_allocation_by_type(fund_code)


# ── 板块实时涨幅 ─────────────────────────────────────────────────


async def _fetch_sector_rise(client: httpx.AsyncClient, sectors_list: list[str]) -> list[dict] | None:
    """抓实时板块涨幅，返回 [{name, rise}]，失败返回 None。"""
    url = "https://api.fund.eastmoney.com/ztjj/GetZTJJListNew"
    params = {"tt": "0", "dt": "syl", "st": "D", "_": str(int(datetime.now().timestamp() * 1000))}

    try:
        raw = (await client.get(url=url, params=params)).text
    except Exception:
        return None

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        payload = json.loads(m.group())
    except json.JSONDecodeError:
        return None

    sector_list = payload.get("Data") or []
    result: list[dict] = []
    for s in sector_list:
        name = s.get("INDEXNAME", "")
        if name in sectors_list:
            try:
                rise = float(s.get("D", 0))
            except (ValueError, TypeError):
                rise = 0.0
            result.append({"name": name, "rise": rise})
    return result if result else None


# ── 适用性检查 & 时效性 ──────────────────────────────────────────


def _check_applicability_by_name(fund_name: str) -> str | None:
    """按基金名拦截不适用类型（债券/QDII/宽基指数）。返回原因或 None（通过）。"""
    _bond_kw = ["债券", "纯债", "信用债", "利率债", "可转债", "偏债", "固收", "短债", "中短债"]
    if any(kw in fund_name for kw in _bond_kw):
        return "该基金为债券型/偏债型基金，净值波动主要由债券资产驱动，暂不支持盘中实时估算。"

    _qdii_kw = ["QDII", "海外", "全球", "纳斯达克", "标普", "恒生", "跨境", "港股通"]
    if any(kw in fund_name for kw in _qdii_kw):
        return "该基金为QDII/跨境基金，底层资产交易时段与A股不一致，A股盘中无法获取境外资产实时价格，暂不支持盘中净值估算。"

    _broad_idx_kw = [
        "沪深300", "中证500", "中证800", "中证1000", "中证2000",
        "上证50", "上证180", "深证100", "深证成指",
        "创业板指", "创业板综", "科创50", "科创100",
        "中证全指", "国证2000", "中证A50", "A500",
    ]
    if any(kw in fund_name for kw in _broad_idx_kw):
        return "该基金为宽基指数基金，持仓横跨多个行业板块，板块代理涨幅严重失真，暂不支持盘中净值估算。"

    return None


def _check_applicability_by_ratio(total_ratio: float, stock_ratio: float) -> str | None:
    """按比例检查：股票仓位<30% 或前十覆盖<60% 不适用。返回原因或 None。"""
    if stock_ratio < 30:
        return (
            f"该基金股票仓位仅 {stock_ratio:.1f}%，属于债券型/偏债型基金，"
            f"净值波动主要由债券资产驱动，暂不支持盘中实时估算。"
        )

    if stock_ratio > 0:
        coverage = total_ratio / stock_ratio
        if coverage < 0.6:
            return (
                f"该基金前十重仓股仅覆盖股票仓位的 {coverage:.0%}（< 60%），"
                f"非重仓股部分依赖板块代理估算，代理偏差占比过大，"
                f"暂不支持盘中净值估算。"
            )

    return None


def _get_timeliness_note(end_date_str: str) -> str | None:
    """按季报截止日 + 20 天近似发布日，返回时效性提示（高/中/低）。"""
    if not end_date_str:
        return None

    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    except ValueError:
        return None

    pub_date = end_date + timedelta(days=20)
    now = datetime.now()

    months = (now.year - pub_date.year) * 12 + (now.month - pub_date.month)
    if now.day < pub_date.day:
        months -= 1

    if months <= 1:
        return "✅ 持仓数据时效：高（距季报发布 ≤1个月，基金经理大幅调仓概率小）"
    elif months <= 2:
        return "⚠️ 持仓数据时效：中（距季报发布 1-2个月，可能有局部调仓）"
    else:
        return "❌ 持仓数据时效：低（距季报发布 >2个月，持仓可能已显著变化，尤其风格漂移型基金，估算结果参考价值下降）"


# ── 核心计算 ─────────────────────────────────────────────────────


async def _calc_nav_estimate(fund_code: str) -> dict:
    """获取持仓+资产配置+板块涨幅，计算估算涨幅，返回结构化 dict。

    成功: {"fund_code": str, "fund_name": str, "estimated_change": float, ...}
    失败: {"fund_code": str, "error": str}
    """
    errors: list[str] = []

    # ── 步骤1：获取持仓（直接取结构化数据，无文本往返）──
    try:
        data = await _fetch_holdings_data(fund_code)
    except Exception as e:
        return {"fund_code": fund_code, "error": str(e)}

    if not data["holdings"]:
        return {"fund_code": fund_code, "error": "持仓解析失败，未提取到任何持仓记录"}

    fund_name = data["fund_name"]
    end_date = data["end_date"]
    theme_tags = data["theme_tags"]
    total_ratio = data["total_ratio"]
    holdings = data["holdings"]

    # ── 适用性检查 A：仅凭基金名称即可判断 ──
    inapplicable = _check_applicability_by_name(fund_name)
    if inapplicable:
        return {
            "fund_code": fund_code, "fund_name": fund_name, "end_date": end_date,
            "error": f"暂不支持估算。{inapplicable}",
        }

    # ── 步骤2 & 3 并发：资产配置 + 板块实时涨幅 ──
    async def _get_alloc() -> dict:
        return await asyncio.to_thread(_get_asset_allocation, fund_code, end_date)

    async def _get_sectors() -> list[dict]:
        if not theme_tags:
            return []
        try:
            async with httpx.AsyncClient(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                    "Referer": "https://fund.eastmoney.com/",
                },
                timeout=30,
            ) as client:
                return await _fetch_sector_rise(client, theme_tags) or []
        except Exception as e:
            errors.append(f"板块涨幅获取异常: {e}")
            return []

    asset_alloc, sector_rises = await asyncio.gather(_get_alloc(), _get_sectors())
    stock_ratio = asset_alloc["股票"]
    alloc_source = asset_alloc.pop("_source")

    # ── 适用性检查 B：依赖资产配置数据 ──
    inapplicable = _check_applicability_by_ratio(total_ratio, stock_ratio)
    if inapplicable:
        return {
            "fund_code": fund_code, "fund_name": fund_name, "end_date": end_date,
            "error": f"暂不支持估算。{inapplicable}",
        }

    # ── 时效性提示 ──
    timeliness_note = _get_timeliness_note(end_date)

    # ── 步骤4：执行估算 ──
    heavy_sum = sum(h["ratio"] * h["change"] for h in holdings)
    heavy_contribution = heavy_sum / 100.0

    light_ratio = stock_ratio - total_ratio
    if light_ratio < 0:
        light_ratio = 0

    if sector_rises:
        sector_avg = sum(s["rise"] for s in sector_rises) / len(sector_rises)
    else:
        sector_avg = sum(h["change"] for h in holdings) / len(holdings) if holdings else 0.0
        if theme_tags:
            errors.append("板块涨幅未匹配到，已用重仓股等权涨幅代理非重仓股部分")

    light_contribution = light_ratio * sector_avg / 100.0
    total_estimate = heavy_contribution + light_contribution

    # ── 持仓贡献明细（带贡献值）──
    holdings_detail = []
    for h in holdings:
        holdings_detail.append({
            "name": h["name"],
            "code": h["code"],
            "ratio": h["ratio"],
            "change": h["change"],
            "contrib": h["ratio"] * h["change"] / 100,
        })

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "end_date": end_date,
        "estimated_change": total_estimate,
        "holdings": holdings_detail,
        "theme_tags": theme_tags,
        "sector_rises": sector_rises,
        "sector_avg": sector_avg,
        "stock_ratio": stock_ratio,
        "total_ratio": total_ratio,
        "heavy_contribution": heavy_contribution,
        "light_contribution": light_contribution,
        "light_ratio": light_ratio,
        "alloc_source": alloc_source,
        "timeliness_note": timeliness_note,
        "errors": errors,
    }


# ── 格式化 ───────────────────────────────────────────────────────


def _format_estimate(d: dict) -> str:
    """_calc_nav_estimate 结果格式化为多段文本（持仓明细/估算结果）。"""
    lines = [
        f"基金代码: {d['fund_code']}",
        f"基金名称: {d['fund_name']}",
        f"持仓截止: {d['end_date']}",
        f"估算时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if d.get("timeliness_note"):
        lines.append(d["timeliness_note"])
    lines += [
        "",
        "━━━ 持仓贡献明细 ━━━",
    ]
    for h in d["holdings"]:
        lines.append(
            f"  {h['name']}({h['code']})  占比 {h['ratio']:.2f}%  "
            f"涨跌 {h['change']:+.2f}%  贡献 {h['contrib']:+.3f}%"
        )

    lines += [
        f"  前十合计占比: {d['total_ratio']:.2f}%  重仓股合计贡献: {d['heavy_contribution']:+.3f}%",
        "",
        "━━━ 估算结果 ━━━",
    ]

    if d.get("sector_rises"):
        sector_str = "、".join(f"{s['name']}({s['rise']:+.2f}%)" for s in d["sector_rises"])
        lines.append(f"  板块实时涨幅: {sector_str}")
        lines.append(f"  板块代理涨幅: {d['sector_avg']:+.2f}%")
    lines.append(f"  股票仓位: {d['stock_ratio']:.1f}%（来源: {d['alloc_source']}）")

    lines += [
        f"  重仓股贡献:     {d['heavy_contribution']:+.3f}%",
        f"  非重仓股贡献:   {d['light_contribution']:+.3f}%"
        f"（非重仓占 {d['light_ratio']:.1f}% × 板块代理 {d['sector_avg']:.2f}%）",
        "  非股票部分:     约 0%（债券/现金波动极小）",
        "  ─────────────────────────",
        f"  估算总涨幅:     {d['estimated_change']:+.2f}%",
    ]

    if d.get("errors"):
        lines.append("")
        for e in d["errors"]:
            lines.append(f"  ⚠ {e}")

    return "\n".join(lines)


# ── 公开接口 ─────────────────────────────────────────────────────


async def estimate_fund_nav(fund_code: str) -> str:
    """预估基金盘中实时净值涨幅（格式化文本输出，供 LLM 阅读）。"""
    result = await _calc_nav_estimate(fund_code)

    if "error" in result:
        fund_name = result.get("fund_name", "")
        end_date = result.get("end_date", "")
        lines = [f"基金代码: {fund_code}"]
        if fund_name:
            lines.append(f"基金名称: {fund_name}")
        if end_date:
            lines.append(f"持仓截止: {end_date}")
        lines += ["", result["error"]]
        return "\n".join(lines)

    return _format_estimate(result)
