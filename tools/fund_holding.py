"""基金持仓 —— 前十重仓股、主攻板块、截止日期。

涨跌幅用腾讯实时行情 API 批量替换页面 HTML 的静态昨日数据。

内部两层：_fetch_holdings_data（结构化 dict）→ get_fund_holdings（格式化文本）。
其他工具可直接调 _fetch_holdings_data 避免文本往返。
"""

import re

import httpx
from bs4 import BeautifulSoup


def _to_tencent_code(page_code: str) -> str:
    """页面 secid → 腾讯行情代码。0.300308 → sz300308, 1.688167 → sh688167。"""
    parts = page_code.split(".")
    if len(parts) == 2:
        prefix = "sh" if parts[0] == "1" else "sz"
        return f"{prefix}{parts[1]}"
    return page_code


async def _fetch_realtime_quotes(
    client: httpx.AsyncClient, codes: list[str]
) -> dict[str, dict]:
    """批量拉 A 股实时行情（腾讯 qt.gtimg.cn，不限频）。一次请求全量。

    返回 {numeric_code: {change_pct, name}}，失败返回空 dict。
    """
    if not codes:
        return {}

    tcodes = [_to_tencent_code(c) for c in codes]
    url = f"http://qt.gtimg.cn/q={','.join(tcodes)}"

    try:
        resp = await client.get(url)
        resp.encoding = "gbk"
    except Exception:
        return {}

    all_quotes: dict[str, dict] = {}
    for line in resp.text.strip().split("\n"):
        if not line.strip() or "=" not in line:
            continue
        # v_sz300308="51~中际旭创~300308~902.01~864.00~..."
        try:
            content = line.split('"', 2)[1]
        except IndexError:
            continue
        fields = content.split("~")
        if len(fields) < 33:
            continue
        try:
            # fields[3]=当前价, fields[32]=涨跌幅%
            code = fields[2]
            change_pct = float(fields[32])
        except (ValueError, IndexError):
            continue
        all_quotes[str(code)] = {
            "change_pct": change_pct,
            "name": fields[1],
        }

    return all_quotes


async def _fetch_holdings_data(fund_code: str) -> dict:
    """拉取并解析基金持仓数据，返回结构化 dict。失败时 raise。

    返回格式::

        {
            "fund_code": "002112",
            "fund_name": "德邦鑫星价值灵活配置混合C",
            "theme_tags": ["光模块"],
            "end_date": "2025-06-30",
            "total_ratio": 71.23,
            "holdings": [
                {"name": "中际旭创", "code": "0.300308", "ratio": 9.92, "change": 13.20},
                ...
            ],
        }
    """
    requests_url = f"https://fund.eastmoney.com/{fund_code}.html?spm=search"

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Host": "fund.eastmoney.com",
        },
        timeout=30,
    ) as client:
        raw = await client.get(url=requests_url)

    soup = BeautifulSoup(raw.text, "html.parser")

    # ── 基金名称 ──
    title_el = soup.select_one(".fundDetail-tit")
    fund_name = title_el.get_text(strip=True) if title_el else ""
    fund_name = re.sub(r"\(\d+\)$", "", fund_name)

    # ── 投资方向（主题标签）──
    theme_container = soup.find("div", class_="themeFund buyFundItemMain popTab")
    theme_tags: list[str] = []
    if theme_container:
        theme_tags = [li.span.get_text(strip=True) for li in theme_container.select(".hd ul li")]

    # ── 持仓截止日期 ──
    end_date_el = soup.find("span", class_="end_date")
    end_date = ""
    if end_date_el:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", end_date_el.get_text(strip=True))
        if m:
            end_date = m.group(1)

    # ── 持仓表格 ──
    table_wrap = soup.find("div", class_="poptableWrap")
    if not table_wrap:
        raise ValueError("未找到持仓数据")

    table = table_wrap.find("table", class_="ui-table-hover")
    if not table:
        raise ValueError("未找到持仓表格")

    # 第一遍：收集股票代码和页面原始涨跌（兜底用）
    stock_entries: list[dict] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        a_tag = tds[0].find("a")
        if not a_tag:
            continue
        stock_name = a_tag.get("title", "") or a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        stock_code = href.rstrip("/").split("/")[-1] if href else ""

        ratio_str = tds[1].get_text(strip=True)
        try:
            ratio = float(ratio_str.replace("%", ""))
        except (ValueError, AttributeError):
            ratio = 0.0

        change_span = tds[2].find("span")
        page_change_str = change_span.get_text(strip=True) if change_span else tds[2].get_text(strip=True)
        try:
            page_change = float(page_change_str.replace("%", ""))
        except (ValueError, AttributeError):
            page_change = 0.0

        stock_entries.append({
            "name": stock_name,
            "code": stock_code,
            "ratio": ratio,
            "page_change": page_change,
        })

    if not stock_entries:
        raise ValueError("未解析到任何持仓记录")

    # ── 批量获取实时行情，替换页面静态涨跌幅 ──
    all_codes = [e["code"] for e in stock_entries if e["code"]]
    realtime_quotes: dict[str, dict] = {}
    if all_codes:
        async with httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=30,
        ) as client:
            realtime_quotes = await _fetch_realtime_quotes(client, all_codes)

    # 组装最终的 holdings 列表
    holdings: list[dict] = []
    for entry in stock_entries:
        page_code = entry["code"]
        numeric_code = page_code.split(".")[-1] if "." in page_code else page_code
        rt = realtime_quotes.get(numeric_code) if numeric_code else None
        change = rt["change_pct"] if rt else entry["page_change"]

        holdings.append({
            "name": entry["name"],
            "code": page_code,
            "ratio": entry["ratio"],
            "change": change,
        })

    # ── 前十持仓占比合计 ──
    total_el = table_wrap.find("span", class_="sum-num")
    total_ratio = 0.0
    if total_el:
        total_str = total_el.get_text(strip=True)
        try:
            total_ratio = float(total_str.replace("%", ""))
        except (ValueError, AttributeError):
            total_ratio = 0.0

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "theme_tags": theme_tags,
        "end_date": end_date,
        "total_ratio": total_ratio,
        "holdings": holdings,
    }


async def get_fund_holdings(fund_code: str) -> str:
    """获取基金持仓（格式化文本输出，供 LLM 阅读）。"""
    try:
        data = await _fetch_holdings_data(fund_code)
    except Exception as e:
        return f"基金代码: {fund_code}\n错误: {e}"

    lines = [
        f"基金代码: {data['fund_code']}",
        f"基金名称: {data['fund_name']}",
    ]
    if data["theme_tags"]:
        lines.append(f"主攻板块方向: {'、'.join(data['theme_tags'])}")
    lines.append(f"截止日期: {data['end_date']}")
    lines.append("")
    lines.append("━━━ 前十持仓 ━━━")
    for h in data["holdings"]:
        lines.append(
            f"  {h['name']}({h['code']})  占比 {h['ratio']:.2f}%  涨跌 {h['change']:+.2f}%"
        )
    lines.append(f"  前十股票持仓占比合计: {data['total_ratio']:.2f}%")
    return "\n".join(lines)
