"""Router 意图分类测试 —— LLM 返回非 JSON 时降级为 DirectAnswer，不抛异常。"""

from unittest.mock import AsyncMock

import core.router as router_module
from core.router import classify_intent
from core.trace import TraceLogger


async def test_classify_intent_falls_back_on_invalid_json(monkeypatch):
    """本地 LLM 偶发输出非 JSON → 降级 DirectAnswer，tools_needed 为空。"""
    monkeypatch.setattr(
        router_module, "local_chat",
        AsyncMock(return_value="这不是合法JSON"),
    )

    trace = TraceLogger()  # 未连接 Redis，走内存 fallback
    decision = await classify_intent(
        [{"role": "user", "content": "任意问题"}],
        history=None,
        trace=trace,
        session_id="test-session",
    )

    assert decision["category"] == "DirectAnswer"
    assert decision["tools_needed"] == []
    assert "降级" in decision["reasoning"]


async def test_classify_intent_parses_valid_json(monkeypatch):
    valid = '{"category": "REWOO", "tools_needed": ["get_fund_performance"], "reasoning": "多维分析"}'
    monkeypatch.setattr(
        router_module, "local_chat",
        AsyncMock(return_value=valid),
    )

    trace = TraceLogger()
    decision = await classify_intent(
        [{"role": "user", "content": "全面分析 519702"}],
        history=None,
        trace=trace,
        session_id="test-session",
    )

    assert decision["category"] == "REWOO"
    assert decision["tools_needed"] == ["get_fund_performance"]
