"""ReAct 执行器纯函数解析测试 —— Thought/Action/Final Answer 解析、参数解析、增量检测。"""

from core.react_loop import _parse_params, _try_parse_action, parse_step


# ── _parse_params ──────────────────────────────────────────────


def test_parse_params_string_value():
    params = _parse_params('keyword="半导体"')
    assert params == {"keyword": "半导体"}


def test_parse_params_array_value():
    params = _parse_params('sectors=["人工智能", "新能源"]')
    assert params == {"sectors": ["人工智能", "新能源"]}


def test_parse_params_mixed_string_and_array():
    params = _parse_params('keyword="半导体", sectors=["人工智能", "新能源"]')
    assert params == {"keyword": "半导体", "sectors": ["人工智能", "新能源"]}


def test_parse_params_empty():
    assert _parse_params("") == {}


# ── parse_step ─────────────────────────────────────────────────


def test_parse_step_action():
    buffer = 'Thought: 需要先搜索基金代码。\nAction: search_fund(keyword="半导体")'
    parsed = parse_step(buffer)
    assert parsed["thought"] == "需要先搜索基金代码。"
    assert parsed["tool"] == "search_fund"
    assert parsed["params"] == {"keyword": "半导体"}


def test_parse_step_final_answer():
    buffer = "Thought: 数据齐全。\nFinal Answer: 该基金近一年表现稳健。"
    parsed = parse_step(buffer)
    assert parsed["thought"] == "数据齐全。"
    assert parsed["final_answer"] == "该基金近一年表现稳健。"


def test_parse_step_final_answer_multiline():
    buffer = "Final Answer: 第一行。\n第二行。\n第三行。"
    parsed = parse_step(buffer)
    assert "final_answer" in parsed
    assert "第一行" in parsed["final_answer"]
    assert "第三行" in parsed["final_answer"]


def test_parse_step_parse_error_when_no_structure():
    parsed = parse_step("一段没有结构化的随机文本")
    assert parsed["thought"] == ""
    assert parsed.get("parse_error") is True


def test_parse_step_final_answer_takes_priority_when_both_present():
    # parse_step 先检测 Final Answer 再检测 Action；buffer 同时含二者时 Final Answer 优先返回
    buffer = (
        'Thought: 先查。\nAction: search_fund(keyword="a")\n'
        'Final Answer: 这是最终答案'
    )
    parsed = parse_step(buffer)
    assert "final_answer" in parsed
    assert parsed["final_answer"] == "这是最终答案"


# ── _try_parse_action（增量检测）──────────────────────────────


def test_try_parse_action_detects_closed_action():
    buffer = 'Thought: x\nAction: search_fund(keyword="a")'
    parsed = _try_parse_action(buffer)
    assert parsed is not None
    assert parsed["tool"] == "search_fund"
    assert parsed["params"] == {"keyword": "a"}


def test_try_parse_action_returns_none_for_final_answer():
    buffer = "Thought: done\nFinal Answer: 答案"
    assert _try_parse_action(buffer) is None


def test_try_parse_action_returns_none_when_unclosed():
    # Action 闭括号未出现，不应误触发
    buffer = 'Thought: x\nAction: search_fund(keyword="a"'
    assert _try_parse_action(buffer) is None
