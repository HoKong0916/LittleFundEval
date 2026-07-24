"""快速话题相关性检测 —— 字符级关键词重叠 + LLM 兜底。

策略：
  1. 提取中文 bigram + 6 位基金代码作为特征集合
  2. 计算 Jaccard 相似度
  3. >0.25 → 直接返回 True（明确相关）
     其余 → fallback 本地 LLM（由 LLM 做语义兜底，防止"上述两个板块"类指代表达被 Jaccard 误杀）
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


async def is_same_topic(current: str, history: list[dict]) -> bool:
    """判断当前问题与历史对话是否属于同一话题（追问 / 对比 / 细化）。

    无历史时返回 False。
    """
    if not history:
        return False

    prev_user = [m["content"] for m in history if m.get("role") == "user"]
    if not prev_user:
        return False

    # 特征提取纳入 assistant 消息：基金代码、板块名等关键信息在助手回复中
    prev_all = [m["content"] for m in history if m.get("role") in ("user", "assistant")]
    prev_text = " ".join(prev_all[-10:])  # 最近 5 轮完整对话

    cur_f = _features(current)
    prev_f = _features(prev_text)

    if not cur_f or not prev_f:
        return True  # 特征不足，保守保留

    overlap = cur_f & prev_f
    union = cur_f | prev_f
    sim = len(overlap) / len(union)

    # ── 快速路径：仅保留高置信度快速通过 ──
    if sim > 0.25:
        return True
    # 其余全部走 LLM 兜底（去除 <0.05 快拒，防止"上述两个板块"类指代表达被误杀）

    # ── 模糊区间 → LLM ──
    # 传递最近 5 轮完整对话（用户+助手），帮助 LLM 理解指代关系
    recent_turns = []
    all_msgs = [m for m in history if m.get("role") in ("user", "assistant")]
    for m in all_msgs[-10:]:  # 最近 5 轮（每轮 user+assistant = 2 条）
        role = "用户" if m["role"] == "user" else "助手"
        recent_turns.append(f"{role}：{m['content']}")
    recent_text = "\n".join(recent_turns)
    system_prompt = SYSTEM_PROMPT_TOPIC.replace("{history_context}", recent_text)
    try:
        result = await local_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": current[:600]},
        ], temperature=0.0)
        return result is not None and "YES" in result.strip().upper()
    except Exception:
        return True  # LLM 不可用，保守保留
