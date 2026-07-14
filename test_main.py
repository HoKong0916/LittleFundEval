import asyncio
import os
import sys

# Windows 终端默认 GBK 无法编码 emoji，强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

from core.router import classify_intent
from core.react_loop import run_react_loop
from core.topic import is_same_topic
from core.direct_answer import run_direct_answer
from core.memory import MemoryManager
from core.summarizer import summarize_session

_SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session_id")


def _load_session_id() -> str:
    """从 `.session_id` 文件加载持久化 session_id，首次运行时自动生成并写入。

    保证同一终端窗口多次运行共用同一会话 ID，历史消息可跨进程复用。
    """
    try:
        with open(_SESSION_FILE) as f:
            sid = f.read().strip()
            if sid:
                return sid
    except FileNotFoundError:
        pass

    # 首次运行：生成新 ID 并写入文件
    from uuid import uuid4
    sid = str(uuid4())
    with open(_SESSION_FILE, "w") as f:
        f.write(sid)
    return sid


async def main():
    """测试入口：检查摘要标记 → 摘要 → 加载历史 → 话题检测 → 路由 → 执行 → 打摘要标记。

    摘要不在 N 轮结束时执行，而是打在标记上，由 N+1 轮启动时同步完成。
    这样摘要永远在两次请求之间的"安全窗口"执行，避免了与 append_message 的并发竞态。
    """
    session_id = _load_session_id()
    user_message = "我当前有持有存储板块的，你觉得我是否需要减仓，减出来的钱，你觉得该在哪个板块建仓呢？"

    async with MemoryManager() as memory:
        # ── N+1 轮开始：检查上一轮是否留下摘要标记 ──
        need_summary = await memory.check_and_clear_summary_flag(session_id)
        if need_summary:
            await summarize_session(memory, session_id)

        # ── 加载历史 ──
        history = await memory.load_messages(session_id)
        has_context = bool(history) and await is_same_topic(user_message, history)

        # ── 路由 ──
        decision = await classify_intent([{"role": "user", "content": user_message}], history if has_context else None)
        print(f"🧭 {decision['category']}: {decision['reasoning']}")

        print(f"\n🚀 执行 {decision['category']} 路径\n")

        # ── 执行 ──
        msg_list = [{"role": "user", "content": user_message}]
        if decision["category"] == "DirectAnswer":
            final_answer = await run_direct_answer(msg_list, history, has_context)
        elif decision["category"] == "ReAct":
            final_answer = await run_react_loop(msg_list, decision["tools_needed"], history, has_context)

        # ── 存入记忆 ──
        if final_answer:
            await memory.append_message(session_id, {"role": "user", "content": user_message})
            await memory.append_message(session_id, {"role": "assistant", "content": final_answer})

            # N 轮结束：只打标记，不执行摘要（留给 N+1 轮启动时处理）
            await memory.set_summary_flag(session_id)
        else:
            print("⚠️ 未获得有效回复，不存入历史")

asyncio.run(main())
