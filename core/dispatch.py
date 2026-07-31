"""工具调度 —— 统一的异步工具调用入口，内置超时 + 1 次重试 + 结构化错误返回。"""
import asyncio

from tools import TOOLS_MAP

# 单次工具调用超时（秒）—— 涵盖 HTTP 请求、解析、数据清洗的全链路
TOOL_TIMEOUT = 30


async def dispatch_tool(tool_name: str, params: dict) -> str:
    """执行工具调用，返回 Observation 文本或结构化错误 JSON。

    工具未注册直接返回 error；首次超时/异常重试 1 次；二次仍失败返回 error JSON。
    """
    fn = TOOLS_MAP.get(tool_name)
    if fn == None:
        return f'{{"status":"error","source":"{tool_name}","msg":"工具未实现"}}'

    for attempt in range(2):                       # 首次 + 1 次重试
        try:
            result = await asyncio.wait_for(fn(**params), timeout=TOOL_TIMEOUT)
            return result if isinstance(result, str) else str(result)
        except (asyncio.TimeoutError, Exception) as e:
            if attempt == 0:
                continue                            # 首次失败，立即重试
            # 二次失败，返回结构化错误供 LLM 感知
            err_type = "timeout" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
            return f'{{"status":"error","source":"{tool_name}","msg":"{e}","type":"{err_type}","retried":true}}'
