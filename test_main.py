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

_SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session_id")


def _load_session_id() -> str:
    """从文件加载 session_id，不存在则创建新的并持久化。"""
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
    session_id = _load_session_id()
    user_message = "消费电子最近表现如何？"

    async with MemoryManager() as memory:
        user_input = [{"role": "user", "content": user_message}]

        # 前置历史检查：如果当前问题是对此前对话的追问/对比，
        # 且历史中已有助手回答，则跳过工具调用，直接用历史数据回答。
        history = await memory.load_messages(session_id)
        if history and is_same_topic(user_message, history):
            decision = {
                "category": "DirectAnswer",
                "tools_needed": [],
                "reasoning": "历史对话中已有相关分析数据，直接利用历史上下文回答",
            }
            print(f"📚 {decision['reasoning']}")
        else:
            decision = classify_intent(user_input)

        print(f"\n🚀 执行 {decision['category']} 路径\n")

        if decision["category"] == "DirectAnswer":
            final_answer = await run_direct_answer(user_input, memory, session_id)
        elif decision["category"] == "ReAct":
            final_answer = await run_react_loop(user_input, decision["tools_needed"], memory, session_id)
        else:
            return

        # 存入本轮对话
        if final_answer:
            await memory.append_message(session_id, {"role": "user", "content": user_message})
            await memory.append_message(session_id, {"role": "assistant", "content": final_answer})


asyncio.run(main())
