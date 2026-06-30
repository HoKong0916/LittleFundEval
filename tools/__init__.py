"""工具注册中心

管理 3 个工具的注册与查询。所有工具均为 async，并发执行。
"""
import asyncio

from tools.fund_nav import get_fund_nav
from tools.fund_holdings import get_fund_holdings
from tools.benchmark import get_benchmark


# 各工具返回结果的冗余字段，发给 LLM 前移除
_TRIM_KEYS = {
    "get_fund_nav": {"source"},
    "get_fund_holdings": {"source", "fund_code", "当前季度持仓明细", "上一季度持仓明细"},
    "get_benchmark": {"source"},
}


class ToolRegistry:
    def __init__(self):
        self._tools = {
            "get_fund_nav": {
                "name": "get_fund_nav",
                "description": "获取基金业绩数据，包含近1/3/6/12月收益率、同类排名、夏普比率、最大回撤、年化波动率、最新净值",
                "func": get_fund_nav,
            },
            "get_fund_holdings": {
                "name": "get_fund_holdings",
                "description": "获取基金最新季度持仓明细，包含前十大重仓股、持仓集中度、换手变化",
                "func": get_fund_holdings,
            },
            "get_benchmark": {
                "name": "get_benchmark",
                "description": "获取基准指数(创业板50)近1/3/6/12月收益，并与基金收益对比计算各窗口超额收益",
                "func": get_benchmark,
            },
        }

    async def call(self, name: str, fund_code: str) -> dict:
        """执行单个工具，返回 trim 后的结果"""
        raw = await self._tools[name]["func"](fund_code)
        if "error" in raw:
            return raw
        return {k: v for k, v in raw.items() if k not in _TRIM_KEYS.get(name, set())}

    async def call_all(self, fund_code: str) -> dict[str, dict]:
        """异步并发执行全部 3 个工具"""

        async def _call_one(name: str) -> tuple[str, dict]:
            try:
                return name, await self.call(name, fund_code)
            except Exception as e:
                return name, {"error": str(e)}

        tasks = [_call_one(name) for name in self._tools]
        gathered = await asyncio.gather(*tasks)
        return dict(gathered)

    def get_tool_schemas(self) -> list[dict]:
        """生成 OpenAI function calling 格式的工具定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fund_code": {"type": "string", "description": "6位基金代码"}
                        },
                        "required": ["fund_code"],
                    },
                },
            }
            for t in self._tools.values()
        ]
