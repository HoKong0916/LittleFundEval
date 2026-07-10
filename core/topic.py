"""快速话题相关性检测 —— 字符级关键词重叠 + LLM 兜底。

策略：
  1. 提取中文 bigram + 6 位基金代码作为特征集合
  2. 计算 Jaccard 相似度
  3. >0.25 → 直接返回 True（明确相关）
     <0.05 → 直接返回 False（明确不相关）
     中间模糊区间 → fallback 本地 LLM

约 80% 请求走快速路径，无需启动 LLM。
"""

import re

from llm_client import local_chat
from prompts.topic import SYSTEM_PROMPT_TOPIC

_FUND_CODE_RE = re.compile(r"\b\d{6}\b")
_CHINESE_RE = re.compile(r"[一-鿿]")


def _features(text: str) -> set[str]:
    """提取特征：6 位基金代码 + 中文 2-gram。"""
    feats: set[str] = set()

    feats.update(_FUND_CODE_RE.findall(text))

    chars = "".join(_CHINESE_RE.findall(text))
    for i in range(len(chars) - 1):
        feats.add(chars[i : i + 2])

    return feats


def is_same_topic(current: str, history: list[dict]) -> bool:
    """判断当前问题与历史对话是否属于同一话题（追问 / 对比 / 细化）。

    无历史时返回 False。
    """
    if not history:
        return False

    prev_user = [m["content"] for m in history if m.get("role") == "user"]
    if not prev_user:
        return False

    prev_text = " ".join(prev_user[-3:])

    cur_f = _features(current)
    prev_f = _features(prev_text)

    if not cur_f or not prev_f:
        return True  # 特征不足，保守保留

    overlap = cur_f & prev_f
    union = cur_f | prev_f
    sim = len(overlap) / len(union)

    # ── 快速路径 ──
    if sim > 0.25:
        return True
    if sim < 0.05:
        return False

    # ── 模糊区间 → LLM ──
    last_q = prev_user[-1]
    prompt = SYSTEM_PROMPT_TOPIC.format(last_q=last_q[:300], current=current[:300])
    try:
        result = local_chat([{"role": "user", "content": prompt}], temperature=0.0)
        return result is not None and "YES" in result.strip().upper()
    except Exception:
        return True  # LLM 不可用，保守保留
