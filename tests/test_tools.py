"""工具注册中心测试 —— Schema 结构一致性、prompt JSON 合法性、函数映射完整性。"""

import json

from tools import TOOLS_MAP, TOOLS_SCHEMA, tools_prompt_json


def test_schema_structure_well_formed():
    for entry in TOOLS_SCHEMA:
        assert entry["type"] == "function"
        fn = entry["function"]
        assert "name" in fn and isinstance(fn["name"], str) and fn["name"]
        assert "description" in fn and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "required" in params and isinstance(params["required"], list)


def test_tools_map_keys_match_schema_names():
    schema_names = {t["function"]["name"] for t in TOOLS_SCHEMA}
    assert set(TOOLS_MAP.keys()) == schema_names


def test_tools_map_values_are_callable():
    for name, fn in TOOLS_MAP.items():
        assert callable(fn), f"{name} 不是可调用对象"


def test_tools_prompt_json_is_valid_json_list():
    raw = tools_prompt_json()
    data = json.loads(raw)
    assert isinstance(data, list)
    assert len(data) == len(TOOLS_SCHEMA)
    # 扁平化后每项应含 name 字段（剥离外层 type 包装）
    for item in data:
        assert "name" in item


def test_tools_prompt_json_contains_all_tool_names():
    raw = tools_prompt_json()
    for entry in TOOLS_SCHEMA:
        assert entry["function"]["name"] in raw
