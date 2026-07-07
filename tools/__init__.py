"""工具注册中心 —— 所有工具的 JSON Schema 定义与函数映射的统一入口。"""

import json

from tools.search_fund import search_fund
from tools.fund_performance import get_fund_performance
from tools.fund_holding import get_fund_holdings
from tools.capital_inflow import capital_inflow_in_sectors
from tools.select_fund import select_fund

# ═══════════════════════════════════════════════════════════════════
# JSON Schema 定义（OpenAI/DeepSeek function calling 格式）
# ═══════════════════════════════════════════════════════════════════

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_fund",
            "description": "根据关键词搜索基金，返回匹配的基金代码和名称",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，如基金名称、代码",
                    }
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fund_performance",
            "description": "获取基金基本面：收益状况（近1/3/6/12月收益率及同类排名）、风险状况（最大回撤、年化波动率、夏普比率、较同类风险收益比、较同类抗风险波动）、当天状况（最新净值、估算净值、估算涨幅）",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {
                        "type": "string",
                        "description": "6位数字基金代码",
                    }
                },
                "required": ["fund_code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fund_holdings",
            "description": "获取基金最新季报的前十大重仓股、主攻板块方向、前十持仓占比合计",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {
                        "type": "string",
                        "description": "6位数字基金代码",
                    }
                },
                "required": ["fund_code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capital_inflow_in_sectors",
            "description": "获取各板块资金流向（今日/近1周/近1月/近3月）。不传sectors则展示各时间段TOP5流入流出板块；传入sectors则按指定板块展示四个时间段的资金流向",
            "parameters": {
                "type": "object",
                "properties": {
                    "sectors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选，指定板块名称列表，如['人工智能', '新能源']。不传则展示各时间段TOP5",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_fund",
            "description": "根据自然语言查询，从天天基金导购页筛选指定板块和时间段下收益前十的基金列表",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_query": {
                        "type": "string",
                        "description": "自然语言查询，描述板块和时间段，如'AI应用板块近1月前十收益'",
                    }
                },
                "required": ["user_query"],
                "additionalProperties": False,
            },
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
# 工具名 → async 函数映射
# ═══════════════════════════════════════════════════════════════════

TOOLS_MAP: dict = {
    "search_fund": search_fund,
    "get_fund_performance": get_fund_performance,
    "get_fund_holdings": get_fund_holdings,
    "capital_inflow_in_sectors": capital_inflow_in_sectors,
    "select_fund": select_fund,
}

# ═══════════════════════════════════════════════════════════════════
# 辅助：供 prompt 使用的扁平化工具列表 JSON
# ═══════════════════════════════════════════════════════════════════


def tools_prompt_json(indent: int = 2) -> str:
    """返回扁平化的工具列表 JSON 字符串，用于嵌入 system prompt。

    OpenAI/DeepSeek API 格式为 {"type":"function","function":{...}}，
    prompt 中只需要 function 对象本身。
    """
    flat = [t["function"] for t in TOOLS_SCHEMA]
    return json.dumps(flat, ensure_ascii=False, indent=indent)
