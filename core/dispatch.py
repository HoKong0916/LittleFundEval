"""工具调度：统一的异步工具调用入口，供 ReAct / REWOO 等执行器复用。"""
from tools import TOOLS_MAP


async def dispatch_tool(tool_name: str, params: dict) -> str:
    """执行工具调用，返回 Observation 文本。"""
    fn = TOOLS_MAP.get(tool_name)
    if fn is None:
        return f"工具 '{tool_name}' 尚未实现"
    try:
        result = await fn(**params)
        return result if isinstance(result, str) else str(result)
    except Exception as e:
        return f"工具调用失败: {e}"
