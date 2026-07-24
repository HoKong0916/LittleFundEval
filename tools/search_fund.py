"""基金搜索工具 —— 根据关键词从天基金搜索API匹配基金代码和名称。"""

import json
import httpx
from datetime import datetime


async def search_fund(keyword: str) -> str:
    """根据关键词搜索基金，返回匹配的基金代码和名称。

    匹配不到时返回语义化提示，引导用户提供更完整的基金名称。
    """
    timestamp_ms = int(datetime.now().timestamp() * 1000)

    requests_url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    requests_payload = {
        "callback": "jQuery183014660270114063068" + str(timestamp_ms),
        "m": "1",
        "key": keyword,
        "_": str(timestamp_ms),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        raw = (await client.get(url=requests_url, params=requests_payload)).text

    # ── JSONP 解包：去掉首尾引号 + jQuery 回调前缀 ──
    raw = raw.strip("'")
    start = raw.find('(') + 1
    end = raw.rfind(')')
    json_str = raw[start:end]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return f'未找到与 "{keyword}" 匹配的基金。请用户提供更完整的基金名称（不要仅用缩写）。'

    if not data.get("Datas"):
        return f'未找到与 "{keyword}" 匹配的基金。请用户提供更完整的基金全称，不要仅用缩写或部分名称。'

    # ── 取首个匹配结果 ──
    lines = [
        "基金代码: " + data["Datas"][0]["CODE"],
        "基金名称: " + data["Datas"][0]["NAME"],
    ]
    return "\n".join(lines)
