"""对话摘要引擎 —— 在 token 超出阈值时从旧→新压缩助手回答。

由调用方在 N+1 轮开始时同步调用（await），不在 N 轮结束时 fire-and-forget：

    N 轮结束:  memory.set_summary_flag(sid)       # 只打标记
    N+1 轮开始: if memory.check_and_clear_summary_flag(sid):  # GETDEL 原子
                   await summarize_session(memory, sid)       # 同步执行
               history = await memory.load_messages(sid)      # 再加载历史

渐进分层摘要（金字塔）：
- L0 = 原文（近期）
- L1 = 3-5 句摘要（中期）
- L2 = 一句话（远期）
- L3 = 一行关键结论（最远期）
- 每条消息始终从原文压缩，prompt 控制粒度，不链式叠加
- _original 保留至消息到达 L3 后才删除，确保跨会话升级压缩时始终有源文可用
- 每轮遍历从旧→新，越旧的消息自然经历越多轮压缩
- 所有层都用完后 token 仍超阈值 → 从最旧消息开始截断
- 摘要永远在两次请求之间的"安全窗口"执行，无并发写入风险
"""

from core.memory import MemoryManager
from llm_client import local_chat
from config import count_tokens, MAX_TOKEN_THRESHOLD
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
    """入口：加载会话消息 → 多轮分层压缩 → 写回。

    顶层捕获所有异常，确保以 fire-and-forget（create_task）方式调用时
    不会因 Redis 断连 / LLM 错误等原因泄露 "Task exception was never retrieved"。

    CLI 场景建议直接 await，确保摘要完成后再退出 async with 块；
    FastAPI 多用户场景用 create_task 即可，本函数的 try/except 保证安全。
    """
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

            source = m.get("_original", m["content"])

            try:
                m["content"] = await _summarize_one(source, target_layer)
                if "_original" not in m:
                    m["_original"] = source
                m["_layer"] = target_layer
                changed = True
            except Exception:
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
