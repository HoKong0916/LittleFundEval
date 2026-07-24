"""基金盘中净值估算工具。

通过重仓股当日涨跌 × 持仓比例 + 板块代理涨幅 × 非重仓仓位，实时估算基金盘中净值变化。
比 fundgz 接口的简单估算更精细——逐只计算持仓贡献，区分重仓股与板块代理两部分。

公式：
    估算涨幅 ≈ 重仓股贡献 + 非重仓股板块代理贡献 + 非股票部分（≈0）

    重仓股贡献   = Σ(单只占净资产比% × 单只当日涨跌幅%) / 100
    非重仓股贡献 = (股票仓位% - 前十合计占比%) × 板块代理涨幅% / 100

适用条件：
    1. 股票仓位 ≥ 30%（排除债券型/偏债型）
    2. 非 QDII / 跨境基金（底层资产需与 A 股同步交易）
    3. 非宽基指数基金（跨板块过多时板块代理失真）
    4. 前十重仓股覆盖股票仓位 ≥ 60%（否则代理偏差占比过大）

数据时效性：
    方法依赖季报披露的持仓数据，可靠性随时间衰减：
        季报发布 ≤1 个月：可信度高
        1-2 个月：可能有局部调仓
        >2 个月：持仓可能已显著变化，参考价值下降
"""

import asyncio
import json
import re

import akshare as ak
import httpx
from datetime import datetime, timedelta

from tools.fund_holding import get_fund_holdings


# ═══════════════════════════════════════════════════════════════════
# 持仓文本解析
# ═══════════════════════════════════════════════════════════════════


def _parse_holdings_text(text: str) -> dict:
    """解析 get_fund_holdings 返回的格式化文本，提取结构化持仓数据。

    返回:
        {
            "fund_name": str,
            "end_date": str,
            "theme_tags": [str],
            "total_ratio": float,         # 前十合计占比（%）
            "holdings": [
                {"name": str, "code": str, "ratio": float, "change": float},
            ],
        }
    """
    result: dict = {
        "fund_name": "",
        "end_date": "",
        "theme_tags": [],
        "total_ratio": 0.0,
        "holdings": [],
    }

    for line in text.split("\n"):
        line = line.strip()

        if line.startswith("基金名称:"):
            result["fund_name"] = line.split(":", 1)[1].strip()
        elif line.startswith("截止日期:"):
            result["end_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("主攻板块方向:"):
            tags_str = line.split(":", 1)[1].strip()
            result["theme_tags"] = [t.strip() for t in tags_str.split("、") if t.strip()]
        elif "前十股票持仓占比合计:" in line:
            m = re.search(r"([\d.]+)", line.split(":", 1)[1])
            if m:
                result["total_ratio"] = float(m.group(1))
        elif "(" in line and "占比" in line and "涨跌" in line:
            # 如:   中际旭创(0.300308)  占比 9.92%  涨跌 13.20%
            m = re.match(r"\s*(.+?)\(([^)]+)\)\s+占比\s+([\d.]+%?)\s+涨跌\s+([+-]?[\d.]+%?)", line)
            if m:
                try:
                    ratio = float(m.group(3).replace("%", ""))
                except ValueError:
                    ratio = 0.0
                try:
                    change = float(m.group(4).replace("%", ""))
                except ValueError:
                    change = 0.0
                result["holdings"].append({
                    "name": m.group(1).strip(),
                    "code": m.group(2).strip(),
                    "ratio": ratio,
                    "change": change,
                })

    return result


# ═══════════════════════════════════════════════════════════════════
# 资产配置（含兜底推断）
# ═══════════════════════════════════════════════════════════════════


def _infer_asset_allocation_by_type(fund_code: str) -> dict:
    """兜底：按基金代码前缀推断默认资产配置。

    基金代码前缀规则：
        ETF/LOF(15/16/51/52) → 股票仓位 ~93%
        普通开放式          → 按混合型 ~70%
    """
    prefix = fund_code[:2] if len(fund_code) >= 2 else ""
    if prefix in ("15", "16", "51", "52"):
        return {"股票": 93.0, "债券": 2.0, "现金": 5.0, "_source": "inferred_etf"}
    return {"股票": 70.0, "债券": 15.0, "现金": 15.0, "_source": "inferred_mixed"}


def portfolio_asset_allocation(fund_code: str, date: str) -> dict[str, str] | None:
    """获取基金最新季报的资产配置比例（股票/债券/现金等）。

    通过 akshare 接口，返回 {"资产类型": "仓位占比%", ...}。
    """
    # ── akshare 接口要求 YYYYMMDD 格式 ──
    date = date.replace("-", "")
    fund_individual_detail_hold_xq_df = ak.fund_individual_detail_hold_xq(symbol=fund_code, date=date)
    result = {k: f"{v}%" for k, v in zip(fund_individual_detail_hold_xq_df['资产类型'], fund_individual_detail_hold_xq_df['仓位占比'])}
    return result


def _get_asset_allocation(fund_code: str, date: str) -> dict:
    """获取基金资产配置比例。

    优先 akshare 精确数据，失败则按基金代码前缀推断默认仓位。
    返回 {"股票": float, "债券": float, "现金": float, "_source": str}。
    """
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


# ═══════════════════════════════════════════════════════════════════
# 板块实时涨幅
# ═══════════════════════════════════════════════════════════════════


async def _fetch_sector_rise(client: httpx.AsyncClient, sectors_list: list[str]) -> list[dict] | None:
    """抓取实时的板块涨幅数据。

    返回 [{"name": "板块名", "rise": 1.23}, ...]，失败返回 None。
    """
    url = "https://api.fund.eastmoney.com/ztjj/GetZTJJListNew"
    params = {"tt": "0", "dt": "syl", "st": "D", "_": str(int(datetime.now().timestamp() * 1000))}

    try:
        raw = (await client.get(url=url, params=params)).text
    except Exception:
        return None

    # ── jQuery callback 解包: jQuery123({...}) → {...} ──
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


# ═══════════════════════════════════════════════════════════════════
# 适用性检查 & 时效性
# ═══════════════════════════════════════════════════════════════════


def _check_applicability_by_name(fund_name: str) -> str | None:
    """根据基金名称做早期拦截：债券型 / QDII / 宽基指数。

    仅依赖基金名称，无需资产配置数据，放在步骤 1 之后、步骤 2 之前。
    返回不适用原因；None 表示通过，需继续用比例检查确认。
    """
    # ── 债券型/偏债型 ──
    _bond_kw = ["债券", "纯债", "信用债", "利率债", "可转债", "偏债", "固收", "短债", "中短债"]
    if any(kw in fund_name for kw in _bond_kw):
        return (
            "该基金为债券型/偏债型基金，净值波动主要由债券资产驱动，"
            "暂不支持盘中实时估算。"
        )

    # ── QDII/跨境基金：交易时段不一致 ──
    _qdii_kw = ["QDII", "海外", "全球", "纳斯达克", "标普", "恒生", "跨境", "港股通"]
    if any(kw in fund_name for kw in _qdii_kw):
        return (
            "该基金为QDII/跨境基金，底层资产交易时段与A股不一致，"
            "A股盘中无法获取境外资产实时价格，暂不支持盘中净值估算。"
        )

    # ── 宽基指数基金：持仓覆盖面过广 ──
    _broad_idx_kw = [
        "沪深300", "中证500", "中证800", "中证1000", "中证2000",
        "上证50", "上证180", "深证100", "深证成指",
        "创业板指", "创业板综", "科创50", "科创100",
        "中证全指", "国证2000", "中证A50", "A500",
    ]
    if any(kw in fund_name for kw in _broad_idx_kw):
        return (
            "该基金为宽基指数基金，持仓横跨多个行业板块，"
            "板块代理涨幅严重失真，暂不支持盘中净值估算。"
        )

    return None


def _check_applicability_by_ratio(total_ratio: float, stock_ratio: float) -> str | None:
    """比例维度的适用性检查：股票仓位 + 前十覆盖率。

    依赖资产配置数据（stock_ratio），放在步骤 2 之后。
    """
    # ── 股票仓位不足 ──
    if stock_ratio < 30:
        return (
            f"该基金股票仓位仅 {stock_ratio:.1f}%，属于债券型/偏债型基金，"
            f"净值波动主要由债券资产驱动，暂不支持盘中实时估算。"
        )

    # ── 前十重仓股覆盖不足：非重仓代理占比过大 ──
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
    """根据季报截止日期估算数据时效性。

    季报发布通常滞后截止日 2-3 周（约 15 个工作日公告窗口），
    这里用截止日 + 20 天近似发布日。

    返回带图标标记的提示字符串，end_date 为空或无法解析时返回 None。
    """
    if not end_date_str:
        return None

    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    except ValueError:
        return None

    pub_date = end_date + timedelta(days=20)  # 近似发布日
    now = datetime.now()

    # 月份差（考虑日对齐）
    months = (now.year - pub_date.year) * 12 + (now.month - pub_date.month)
    if now.day < pub_date.day:
        months -= 1

    if months <= 1:
        return "✅ 持仓数据时效：高（距季报发布 ≤1个月，基金经理大幅调仓概率小）"
    elif months <= 2:
        return "⚠️ 持仓数据时效：中（距季报发布 1-2个月，可能有局部调仓）"
    else:
        return "❌ 持仓数据时效：低（距季报发布 >2个月，持仓可能已显著变化，尤其风格漂移型基金，估算结果参考价值下降）"


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════


async def estimate_fund_nav(fund_code: str) -> str:
    """预估基金实时净值涨幅。

    执行流程：
        1. 获取基金前十持仓 → 解析出重仓股、主攻板块、截止日期
        2. 适用性检查 A：仅凭基金名排查（QDII / 宽基指数 / 债券型）→ 不适用则直接返回
        3. 并发获取：资产配置（akshare + to_thread） + 板块实时涨幅（HTTP）
        4. 适用性检查 B：比例排查（股票仓位 < 30% / 前十覆盖率 < 60%）→ 不适用则返回
        5. 计算：重仓股加权贡献 + 非重仓股板块代理贡献 + 时效性提示
        6. 格式化输出，含每只重仓股的贡献明细

    每步失败均有兜底，不会因单个接口挂了就整体报错。
    返回 LLM 可直接使用的格式化文本。
    """
    errors: list[str] = []

    # ── 步骤1：获取持仓 ──
    holdings_text = await get_fund_holdings(fund_code)
    if "错误" in holdings_text or "未找到" in holdings_text:
        return holdings_text

    data = _parse_holdings_text(holdings_text)
    if not data["holdings"]:
        return f"基金代码: {fund_code}\n错误: 持仓解析失败，未提取到任何持仓记录"

    fund_name = data["fund_name"]
    end_date = data["end_date"]
    theme_tags = data["theme_tags"]
    total_ratio = data["total_ratio"]
    holdings = data["holdings"]

    # ── 适用性检查 A：仅凭基金名称即可判断（QDII / 宽基 / 债券）──
    inapplicable = _check_applicability_by_name(fund_name)
    if inapplicable:
        return (
            f"基金代码: {fund_code}\n"
            f"基金名称: {fund_name}\n"
            f"持仓截止: {end_date}\n"
            f"\n"
            f"暂不支持估算。{inapplicable}"
        )

    # ── 步骤2 & 3 并发：资产配置（akshare 同步阻塞 → to_thread） + 板块实时涨幅（HTTP）──
    async def _get_alloc() -> dict:
        """akshare 是同步阻塞调用，用 to_thread 避免卡事件循环。

        _get_asset_allocation 内部已统一兜底，此处不再重复。
        """
        return await asyncio.to_thread(_get_asset_allocation, fund_code, end_date)

    async def _get_sectors() -> list[dict]:
        if not theme_tags:
            return []
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=15,
            ) as client:
                return await _fetch_sector_rise(client, theme_tags) or []
        except Exception as e:
            errors.append(f"板块涨幅获取异常: {e}")
            return []

    asset_alloc, sector_rises = await asyncio.gather(_get_alloc(), _get_sectors())
    stock_ratio = asset_alloc["股票"]
    alloc_source = asset_alloc.pop("_source")

    # ── 适用性检查 B：依赖资产配置数据（股票仓位 / 前十覆盖率）──
    inapplicable = _check_applicability_by_ratio(total_ratio, stock_ratio)
    if inapplicable:
        return (
            f"基金代码: {fund_code}\n"
            f"基金名称: {fund_name}\n"
            f"持仓截止: {end_date}\n"
            f"\n"
            f"暂不支持估算。{inapplicable}"
        )

    # ── 时效性提示 ──
    timeliness_note = _get_timeliness_note(end_date)

    # ── 步骤4：执行估算计算 ──
    # 公式：估算涨幅 = 重仓股加权贡献 + 非重仓股板块代理贡献 + 非股票部分（≈0）
    #
    # 重仓股贡献   = Σ(单只占净资产比% × 单只当日涨跌幅%) / 100
    #   例：中际旭创占比 9.92%，涨 +3.20% → 贡献 9.92×3.20/100 = +0.317%
    #
    # 非重仓股贡献 = (股票仓位% - 前十合计占比%) × 板块代理涨幅% / 100
    #   例：非重仓占 24.7%，板块平均涨 +1.35% → 贡献 24.7×1.35/100 = +0.333%
    #
    # 非股票部分   = 债券/现金当日波动极小，近似为 0
    heavy_sum = sum(h["ratio"] * h["change"] for h in holdings)
    heavy_contribution = heavy_sum / 100.0

    # 非重仓股占净资产 = 股票仓位 - 前十合计
    light_ratio = stock_ratio - total_ratio
    if light_ratio < 0:
        light_ratio = 0

    # ── 板块代理涨幅（优先板块实时数据，失败则用重仓股等权涨幅兜底）──
    if sector_rises:
        sector_avg = sum(s["rise"] for s in sector_rises) / len(sector_rises)
    else:
        sector_avg = sum(h["change"] for h in holdings) / len(holdings) if holdings else 0.0
        if theme_tags:
            errors.append("板块涨幅未匹配到，已用重仓股等权涨幅代理非重仓股部分")

    light_contribution = light_ratio * sector_avg / 100.0
    total_estimate = heavy_contribution + light_contribution

    # ── 格式化输出 ──
    lines = [
        f"基金代码: {fund_code}",
        f"基金名称: {fund_name}",
        f"持仓截止: {end_date}",
        f"估算时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if timeliness_note:
        lines.append(timeliness_note)
    lines += [
        "",
        "━━━ 持仓贡献明细 ━━━",
    ]
    for h in holdings:
        contrib = h["ratio"] * h["change"] / 100
        lines.append(
            f"  {h['name']}({h['code']})  占比 {h['ratio']:.2f}%  "
            f"涨跌 {h['change']:+.2f}%  贡献 {contrib:+.3f}%"
        )

    lines += [
        f"  前十合计占比: {total_ratio:.2f}%  重仓股合计贡献: {heavy_contribution:+.3f}%",
        "",
        "━━━ 估算结果 ━━━",
    ]

    if sector_rises:
        sector_str = "、".join(f"{s['name']}({s['rise']:+.2f}%)" for s in sector_rises)
        lines.append(f"  板块实时涨幅: {sector_str}")
        lines.append(f"  板块代理涨幅: {sector_avg:+.2f}%")
    lines.append(f"  股票仓位: {stock_ratio:.1f}%（来源: {alloc_source}）")

    lines += [
        f"  重仓股贡献:     {heavy_contribution:+.3f}%",
        f"  非重仓股贡献:   {light_contribution:+.3f}%（非重仓占 {light_ratio:.1f}% × 板块代理 {sector_avg:+.2f}%）",
        f"  非股票部分:     约 0%（债券/现金波动极小）",
        f"  ─────────────────────────",
        f"  估算总涨幅:     {total_estimate:+.2f}%",
    ]

    if errors:
        lines.append("")
        for e in errors:
            lines.append(f"  ⚠ {e}")

    return "\n".join(lines)
