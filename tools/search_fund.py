import json
import httpx
from datetime import datetime

async def search_fund(keyword: str) -> str:
    timestamp_ms = int(datetime.now().timestamp() * 1000)

    requests_url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    requests_payload = {
        "callback": "jQuery183014660270114063068" + str(timestamp_ms),
        "m": "1",
        "key": keyword,
        "_": str(timestamp_ms),
    }

    async with httpx.AsyncClient() as client:
        raw = (await client.get(url=requests_url, params=requests_payload)).text
    # 步骤1：去掉首尾的单引号
    raw = raw.strip("'")

    # 步骤2：去掉 jQuery 回调函数前缀和后缀括号
    # 找到第一个 '(' 和最后一个 ')'
    start = raw.find('(') + 1
    end = raw.rfind(')')
    json_str = raw[start:end]

    # 步骤3：解析 JSON
    data = json.loads(json_str)

    lines = []

    first_code = data["Datas"][0]["CODE"]
    lines.append("基金代码: " + first_code)
    first_name = data["Datas"][0]["NAME"]
    lines.append("基金名称: " + first_name)

    return "\n".join(lines)
