"""话题相关性检测测试 —— 特征提取 + 边界条件。"""

from core.topic import _features, is_same_topic


def test_features_extract_fund_code():
    feats = _features("519702 最近怎么样")
    assert "519702" in feats


def test_features_extract_chinese_bigrams():
    feats = _features("人工智能板块")
    assert "人工" in feats
    assert "智能" in feats


def test_features_empty_for_pure_ascii():
    # 纯 ASCII 无基金代码、无中文 → 空特征集
    assert _features("abc def") == set()


async def test_is_same_topic_no_history():
    assert await is_same_topic("任意问题", []) is False


async def test_is_same_topic_none_history():
    assert await is_same_topic("任意问题", None) is False
