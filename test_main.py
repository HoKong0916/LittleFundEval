import asyncio
import os
import sys

# Windows 终端默认 GBK 无法编码 emoji，强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

from core.router import classify_intent
from core.react_loop import run_react_loop
from core.rewoo_loop import run_rewoo_loop
from core.topic import is_same_topic
from core.direct_answer import run_direct_answer
from core.memory import MemoryManager
from core.trace import TraceLogger
from core.summarizer import summarize_session

_SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session_id")


def _load_session_id() -> str:
    """从 `.session_id` 文件加载持久化 session_id，首次运行时自动生成并写入。"""
    try:
        with open(_SESSION_FILE) as f:
            sid = f.read().strip()
            if sid:
                return sid
    except FileNotFoundError:
        pass

    from uuid import uuid4
    sid = str(uuid4())
    with open(_SESSION_FILE, "w") as f:
        f.write(sid)
    return sid


async def main():
    """测试入口：trace → 摘要 → 历史 → 话题检测 → 路由 → 执行 → 记忆存储。"""
    session_id = _load_session_id()
    user_message = "今日什么板块表现最佳？"

    async with MemoryManager() as memory, TraceLogger() as trace:
        # ── N+1 轮开始：检查上一轮是否留下摘要标记 ──
        # 摘要标记由上一轮的 append_message 写入（每轮对话存储后置标记）。
        # 如果标记存在 → 上一次对话已结束，触发摘要压缩（旧消息 → 摘要文本，释放上下文窗口）。
        need_summary = await memory.check_and_clear_summary_flag(session_id)
        if need_summary:
            await summarize_session(memory, session_id)

        # ── 加载历史 ──
        history = await memory.load_messages(session_id)
        has_context = bool(history) and await is_same_topic(user_message, history)

        # ── 路由 ──
        decision = await classify_intent(
            [{"role": "user", "content": user_message}],
            history if has_context else None,
            trace=trace, session_id=session_id,
        )

        print(f"\n🚀 执行 {decision['category']} 路径\n")

        # ── 执行 ──
        msg_list = [{"role": "user", "content": user_message}]
        if decision["category"] == "DirectAnswer":
            final_answer = await run_direct_answer(msg_list, history, has_context,
                                                   trace, session_id)
        elif decision["category"] == "ReAct":
            final_answer = await run_react_loop(msg_list, decision["tools_needed"],
                                                history, has_context,
                                                trace, session_id)
        elif decision["category"] == "REWOO":
            final_answer = await run_rewoo_loop(msg_list, decision["tools_needed"],
                                                history, has_context,
                                                trace, session_id)
        else:
            final_answer = ""

        # ── 存入记忆 ──
        if final_answer:
            await memory.append_message(session_id, {"role": "user", "content": user_message})
            await memory.append_message(session_id, {"role": "assistant", "content": final_answer})
            await memory.set_summary_flag(session_id)
        else:
            print("⚠️ 未获得有效回复，不存入历史")

asyncio.run(main())
