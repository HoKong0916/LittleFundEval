"""对话摘要引擎 —— 在 token 超出阈值时从旧→新压缩助手回答。

由调用方在 append_message 之后 fire-and-forget 调用：
    asyncio.create_task(summarize_session(memory, sid))

规则：
- 只压缩 assistant 消息，user 消息不动
- 从最旧回答开始，摘完一条重算一次，total ≤ 阈值即停
- 极端情况（5 条全摘完仍超阈值）不再处理
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

    Args:
        memory: MemoryManager 实例
        session_id: 会话 ID
    """
    messages = await memory.load_messages(session_id)
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
        await memory._overwrite_messages(session_id, messages)
