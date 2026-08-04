"""工具调度测试 —— 未注册工具返回结构化错误，不抛异常。"""

import json

from core.dispatch import dispatch_tool


async def test_dispatch_unknown_tool_returns_error_json():
    result = await dispatch_tool("does_not_exist", {})
    data = json.loads(result)
    assert data["status"] == "error"
    assert data["source"] == "does_not_exist"
    assert "未实现" in data["msg"]
