"""LLM 客户端纯函数测试 —— token 预算 key 格式、Redis URL 构造。"""

import re

from llm_client import _budget_key, _build_redis_url


def test_budget_key_format():
    key = _budget_key("alice")
    # lg:budget:{session_id}:{YYYYMMDD}
    assert re.fullmatch(r"lg:budget:alice:\d{8}", key), key


def test_budget_key_includes_session_id():
    assert _budget_key("user-123").startswith("lg:budget:user-123:")


def test_build_redis_url_default_shape():
    url = _build_redis_url()
    # 默认 host/port 由环境变量决定，结构必须合法
    assert re.fullmatch(r"redis://[^:/]+:\d+/0", url), url
