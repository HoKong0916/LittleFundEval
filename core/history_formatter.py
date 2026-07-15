"""历史对话格式化工具。
把 `history: list[dict]`（role + content）转成 LLM 可直接用的纯文本上下文。
"""


def format_history_dialogue(
    history: list[dict],
    header: str | None = None,
    truncate: int | None = None,
) -> str:
    """把历史对话格式化为 '用户：...\\n助手：...' 纯文本。"""
    if not history:
        return ""
    lines: list[str] = []
    if header:
        lines.append(header)
    for msg in history:
        content = msg.get("content", "")
        if truncate:
            content = content[:truncate]
        role = msg.get("role", "")
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"助手：{content}")
    return "\n".join(lines)


def format_history_assistant_only(history: list[dict]) -> str:
    """只提取历史中助手的回复内容（去标签），用于综合已有数据。"""
    if not history:
        return ""
    lines: list[str] = []
    for msg in history:
        if msg.get("role") == "assistant":
            lines.append(msg.get("content", ""))
            lines.append("")
    return "\n".join(lines)
