"""
从天天基金"基金导购"页面，根据用户自然语言查询，筛选指定板块 + 时间段下收益前十的基金。
"""

import json
import httpx
from bs4 import BeautifulSoup
from llm_client import local_chat
from prompts.select_fund import SYSTEM_PROMPT_SELECT_FUND

URL = "https://fund.eastmoney.com/daogou/#dt4;ft;rs;sd;ed;pr;cp;rt;tp;rk;se;nx;sc1n;stdesc;pi1;pn20;zfdiy;shlist"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}

SORT_DICT = {
    "近1周": "z",
    "近1月": "1y",
    "近3月": "3y",
    "近6月": "6y",
    "今年来": "jn",
    "近1年": "1n",
}


# ── 抓取板块标签 ──

def _fetch_board_tags() -> list[dict[str, str]]:
    """抓取板块标签列表，每项含 id（去掉前3字符）和 title。"""
    resp = httpx.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    tags = []
    for a in soup.select("#content_tp a[id^='tp_']"):
        raw_id = a.get("id", "")
        title = a.get("title", "")
        if raw_id and title:
            tags.append({"id": raw_id[3:], "title": title})

    return tags


# ── LLM 匹配板块 + 时间段 ──

async def _match_user_query(user_query: str, tags: list[dict[str, str]]) -> dict:
    """用本地 LLM 将用户自然语言映射到 (板块ID, 排序key)。

    返回:
        {"sector_id": "BK123456", "sector_title": "人工智能", "sort_key": "1y", "sort_label": "近1月"}
    """
    tags_text = "\n".join(f"- {t['title']}（ID: {t['id']}）" for t in tags)
    sort_text = "\n".join(f"- {label}（key: {key}）" for label, key in SORT_DICT.items())

    system_prompt = SYSTEM_PROMPT_SELECT_FUND.format(tags_text=tags_text, sort_text=sort_text)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]

    response = await local_chat(messages, temperature=0.0)
    return json.loads(response)


# ── 主入口 ──

async def select_fund(user_query: str) -> str:
    """根据用户自然语言查询，返回指定板块 + 时间段下收益前十的基金（格式化文本）。

    示例:
        result = await select_fund("AI应用板块近1月前十收益")
    """
    tags = _fetch_board_tags()
    match = await _match_user_query(user_query, tags)

    requests_url = "https://fund.eastmoney.com/data/FundGuideapi.aspx"
    requests_payload = {
        "dt": "4",
        "sd": "",
        "ed": "",
        "tp": match["sector_id"],
        "sc": match["sort_key"],
        "st": "desc",
        "pi": "1",
        "pn": "10",
        "zf": "diy",
        "sh": "list",
        "rnd": "0.8552703512900851",
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        resp = await client.get(url=requests_url, params=requests_payload)
        # 响应是 JSONP 格式: var rankData = {...};
        raw = resp.text.strip()
        raw = raw.removeprefix("var rankData =").removesuffix(";").strip()
        data = json.loads(raw)

    funds: list[str] = []
    for item in data.get("datas", []):
        parts = item.split(",")
        if len(parts) >= 2:
            funds.append(f"  {parts[0]}: {parts[1]}")

    if not funds:
        funds = ["  （该板块在当前时间段下暂无数据）"]

    lines = [
        f"板块: {match['sector_title']}",
        f"排序: {match['sort_label']}",
        "",
        "━━━ 前十基金 ━━━",
        *funds,
    ]
    return "\n".join(lines)
