"""基金持仓查询工具 —— 抓取天天基金前十重仓股、主攻板块、截止日期。

涨跌幅通过东方财富实时行情 API 批量查询，替换页面 HTML 中的静态（昨日）数据。
"""

import re

import httpx
from bs4 import BeautifulSoup


async def _fetch_realtime_quotes(client: httpx.AsyncClient, codes: list[str]) -> dict[str, dict]:
    """批量获取 A 股实时行情（涨跌幅、股票名称）。

    API: push2.eastmoney.com — 单次请求最多 ~50 只，按 50 分批。
    codes 已是页面提取的 secid 格式（如 "0.300308" / "1.688167"），无需再转换。
    返回 {code: {"change_pct": float, "name": str}, ...}。
    失败返回空 dict，调用方用页面原始数据兜底。
    """
    if not codes:
        return {}

    all_quotes: dict[str, dict] = {}
    batch_size = 50

    for i in range(0, len(codes), batch_size):
        batch = codes[i : i + batch_size]
        secids = ",".join(batch)
        url = (
            f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f3,f12,f14&secids={secids}"
        )
        try:
            resp = await client.get(url)
            data = resp.json()
        except Exception:
            continue

        if not data or data.get("data") is None:
            continue

        for item in data["data"].get("diff", []) or []:
            code = item.get("f12", "")
            if not code:
                continue
            try:
                change_pct = float(item.get("f3", 0) or 0)
            except (ValueError, TypeError):
                change_pct = 0.0

            all_quotes[code] = {
                "change_pct": change_pct,
                "name": item.get("f14", ""),
            }

    return all_quotes


async def get_fund_holdings(fund_code: str) -> str:
    """获取基金前十持仓信息，返回格式化文本。

    涨跌幅替换为东方财富实时行情（API 失败时回退到页面 HTML 原始数据）。
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
    # 去掉末尾的基金代码括号，如 "德邦鑫星价值灵活配置混合C(002112)"
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
        return f"基金代码: {fund_code}\n错误: 未找到持仓数据"

    table = table_wrap.find("table", class_="ui-table-hover")
    if not table:
        return f"基金代码: {fund_code}\n错误: 未找到持仓表格"

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
        # 页面 href 格式: //quote.eastmoney.com/unify/r/0.300308
        # 取最后一个路径段即为 secid（如 "0.300308" / "1.688167"）
        stock_code = href.rstrip("/").split("/")[-1] if href else ""

        ratio = tds[1].get_text(strip=True)

        change_span = tds[2].find("span")
        page_change = change_span.get_text(strip=True) if change_span else tds[2].get_text(strip=True)

        stock_entries.append({
            "name": stock_name,
            "code": stock_code,
            "ratio": ratio,
            "page_change": page_change,
        })

    if not stock_entries:
        return f"基金代码: {fund_code}\n错误: 未解析到任何持仓记录"

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

    # 如果实时行情全部失败，用页面原始数据兜底
    holdings: list[str] = []
    for entry in stock_entries:
        page_code = entry["code"]
        # 页面 code 格式为 "0.300308" / "1.688167"，API 返回的 key 是纯数字 "300308" / "688167"
        numeric_code = page_code.split(".")[-1] if "." in page_code else page_code
        rt = realtime_quotes.get(numeric_code) if numeric_code else None
        if rt:
            change = f"{rt['change_pct']:+.2f}%"
        else:
            change = entry["page_change"]  # 回退到页面原始数据

        holdings.append(
            f"  {entry['name']}({page_code})  占比 {entry['ratio']}  涨跌 {change}"
        )

    # ── 前十持仓占比合计 ──
    total_el = table_wrap.find("span", class_="sum-num")
    total_ratio = total_el.get_text(strip=True) if total_el else ""

    # ── 格式化输出 ──
    lines = [
        f"基金代码: {fund_code}",
        f"基金名称: {fund_name}",
        f"主攻板块方向: {'、'.join(theme_tags)}" if theme_tags else None,
        f"截止日期: {end_date}",
    ]
    lines += [
        "",
        "━━━ 前十持仓 ━━━",
        *holdings,
        f"  前十股票持仓占比合计: {total_ratio}",
    ]
    return "\n".join(line for line in lines if line is not None)
