"""对话摘要引擎 —— 在 token 超出阈值时从旧→新压缩助手回答。

由调用方在 N+1 轮开始时同步调用（await），不在 N 轮结束时 fire-and-forget：

    N 轮结束:  memory.set_summary_flag(sid)       # 只打标记
    N+1 轮开始: if memory.check_and_clear_summary_flag(sid):  # GETDEL 原子
                   await summarize_session(memory, sid)       # 同步执行
               history = await memory.load_messages(sid)      # 再加载历史

规则：
- 只压缩 assistant 消息，user 消息不动
- 从最旧回答开始，摘完一条重算一次，total ≤ 阈值即停
- 极端情况（5 条全摘完仍超阈值）不再处理
- 摘要永远在两次请求之间的"安全窗口"执行，无并发写入风险
"""

import tiktoken

from llm_client import local_chat
from prompts.summarizer import SYSTEM_PROMPT_SUMMARIZER

# tiktoken 编码器：o200k_base 与 DeepSeek tokenizer 高度接近
_TOKEN_ENC = tiktoken.get_encoding("o200k_base")

# 单会话 token 超此阈值触发摘要化
MAX_TOKEN_THRESHOLD = 10000


def count_tokens(text: str) -> int:
    """使用 tiktoken (o200k_base) 精确计算 token 数。"""
    return len(_TOKEN_ENC.encode(text))


async def _summarize_one(content: str) -> str:
    """调用本地模型压缩单条助手回答。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_SUMMARIZER},
        {"role": "user", "content": content},
    ]
    return await local_chat(messages, temperature=0.0)


async def summarize_session(memory, session_id: str) -> None:
    """入口：加载会话消息 → 判断是否需要摘要 → 逐条压缩 → 写回。

    顶层捕获所有异常，确保以 fire-and-forget（create_task）方式调用时
    不会因 Redis 断连 / LLM 错误等原因泄露 "Task exception was never retrieved"。

    CLI 场景建议直接 await，确保摘要完成后再退出 async with 块；
    FastAPI 多用户场景用 create_task 即可，本函数的 try/except 保证安全。

    Args:
        memory: MemoryManager 实例
        session_id: 会话 ID
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
    for m in messages:
        if m.get("role") != "assistant":
            continue
        if m.get("summarized"):
            continue

        try:
            m["content"] = await _summarize_one(m["content"])
            m["summarized"] = True
            changed = True
        except Exception:
            continue

        total = sum(count_tokens(m["content"]) for m in messages)
        if total <= MAX_TOKEN_THRESHOLD:
            break

    if changed:
        try:
            await memory._overwrite_messages(session_id, messages)
        except Exception:
            pass
