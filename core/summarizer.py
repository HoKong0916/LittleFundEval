"""对话摘要引擎 —— token 超阈值时从旧→新渐进压缩助手回答。

金字塔分层：L0 原文 → L1 三五句 → L2 一句话 → L3 关键结论。
每条消息从原文压缩，不链式叠加；_original 保留到 L3 后才删除。
摘要只在 N+1 轮开始的"安全窗口"执行（N 轮结束仅打标），无并发写入风险。
"""

from config import count_tokens, MAX_TOKEN_THRESHOLD
from core.memory import MemoryManager
from llm_client import local_chat
from prompts.summarizer import (
    SYSTEM_PROMPT_SUMMARIZER_L1,
    SYSTEM_PROMPT_SUMMARIZER_L2,
    SYSTEM_PROMPT_SUMMARIZER_L3,
)

_MAX_LAYER = 3

_LAYER_PROMPTS = {
    1: SYSTEM_PROMPT_SUMMARIZER_L1,
    2: SYSTEM_PROMPT_SUMMARIZER_L2,
    3: SYSTEM_PROMPT_SUMMARIZER_L3,
}


async def _summarize_one(content: str, layer: int) -> str:
    """调用本地模型按指定压缩等级压缩单条内容。"""
    prompt = _LAYER_PROMPTS[layer]
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": content},
    ]
    return await local_chat(messages, temperature=0.0)


async def summarize_session(memory: MemoryManager, session_id: str) -> None:
    """加载消息 → 多轮分层压缩 → 写回。顶层捕获所有异常，fire-and-forget 调用也安全。"""
    try:
        messages = await memory.load_messages(session_id)
    except Exception:
        return

    if len(messages) < 2:
        return

    total = sum(count_tokens(m["content"]) for m in messages)
    if total <= MAX_TOKEN_THRESHOLD:
        return

    changed = False

    # ── 渐进压缩：L0→L1→L2→L3，每层从原文压，越旧越浓 ──
    for target_layer in range(1, _MAX_LAYER + 1):
        for m in messages:
            if m.get("role") != "assistant":
                continue
            current_layer = m.get("_layer", 0)
            if current_layer >= target_layer:
                continue
            # 上次本层压缩失败，跳过避免无限重试（否则 _layer 永远停在当前值，
            # 每次会话触发摘要都从原文重试这条，且每次都可能失败）
            if m.get("_layer_failed") == target_layer:
                continue

            source = m.get("_original", m["content"])

            try:
                m["content"] = await _summarize_one(source, target_layer)
                if "_original" not in m:
                    m["_original"] = source
                m["_layer"] = target_layer
                m.pop("_layer_failed", None)  # 成功后清除失败标记
                changed = True
            except Exception:
                m["_layer_failed"] = target_layer  # 标记本层已失败，下次跳过
                changed = True
                continue

            total = sum(count_tokens(m["content"]) for m in messages)
            if total <= MAX_TOKEN_THRESHOLD:
                break

        if total <= MAX_TOKEN_THRESHOLD:
            break

    # ── 兜底截断：所有层用完后 token 仍超阈值，从最旧开始丢 ──
    if total > MAX_TOKEN_THRESHOLD:
        while len(messages) > 2:  # 至少保留一轮对话
            messages.pop(0)
            changed = True
            total = sum(count_tokens(m["content"]) for m in messages)
            if total <= MAX_TOKEN_THRESHOLD:
                break

    if changed:
        for m in messages:
            if m.get("_layer", 0) >= _MAX_LAYER:
                m.pop("_original", None)  # L3 到头了，原文可以丢
        try:
            await memory._overwrite_messages(session_id, messages)
        except Exception:
            pass
