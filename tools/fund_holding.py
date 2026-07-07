import re
import httpx
from bs4 import BeautifulSoup


async def get_fund_holdings(fund_code: str) -> str:
    """获取基金前十持仓信息，返回格式化文本。"""

    requests_url = f"https://fund.eastmoney.com/{fund_code}.html"

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Host": "fund.eastmoney.com",
        }
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

    holdings: list[str] = []
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

        ratio = tds[1].get_text(strip=True)

        change_span = tds[2].find("span")
        change = change_span.get_text(strip=True) if change_span else tds[2].get_text(strip=True)

        holdings.append(f"  {stock_name}({stock_code})  占比 {ratio}  涨跌 {change}")

    # ── 前十持仓占比合计 ──
    total_el = table_wrap.find("span", class_="sum-num")
    total_ratio = total_el.get_text(strip=True) if total_el else ""

    # ── 格式化输出 ──
    lines = [
        f"基金代码: {fund_code}",
        f"基金名称: {fund_name}",
        f"主攻板块方向: {'、'.join(theme_tags)}" if theme_tags else None,
        f"截止日期: {end_date}",
        "",
        "━━━ 前十持仓 ━━━",
        *holdings,
        f"  前十持仓占比合计: {total_ratio}",
    ]
    return "\n".join(line for line in lines if line is not None)
