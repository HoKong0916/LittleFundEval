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
    """测试入口：加载历史 → 话题检测 → 路由分发 → 执行 → 存入记忆 → 异步摘要化。"""
    session_id = _load_session_id()
    user_message = "上述两个板块让你选，你觉得近期哪个适合建仓？"

    async with MemoryManager() as memory:
         # ── 只加载一次 ──
        history = await memory.load_messages(session_id)
        has_context = bool(history) and await is_same_topic(user_message, history)

        # ── 路由 ──
        if has_context:
            decision = {"category": "DirectAnswer", "tools_needed": [], "reasoning": "历史对话中已有相关分析数据，直接利用历史上下文回答"}
            print(f"📚 {decision['reasoning']}")
        else:
            decision = await classify_intent([{"role": "user", "content": user_message}])
        
        print(f"\n🚀 执行 {decision['category']} 路径\n")

        # ── 执行，把 history 和 has_context 传下去 ──
        if decision["category"] == "DirectAnswer":
            final_answer = await run_direct_answer(user_message, history, has_context)
        elif decision["category"] == "ReAct":
            final_answer = await run_react_loop(user_message, decision["tools_needed"],history, has_context)

        # ── 成功后才成对存入 ──
        if final_answer:
            await memory.append_message(session_id, {"role": "user", "content": user_message})
            await memory.append_message(session_id, {"role": "assistant", "content": final_answer})

            # 异步摘要化：fire-and-forget，不阻塞当前响应
            asyncio.create_task(summarize_session(memory, session_id))
        else:
            print("⚠️ 未获得有效回复，不存入历史")

asyncio.run(main())
