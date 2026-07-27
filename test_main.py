import asyncio
import os
import sys

# Windows 终端默认 GBK 无法编码 emoji，强制 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

from core.memory import MemoryManager
from core.trace import TraceLogger
from core.chat import run_chat

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
    """CLI 测试入口：复用 run_chat 编排管道，on_chunk=None 退化为 print 模式。"""
    session_id = _load_session_id()
    user_message = "给出这六个基金实时预估涨幅，分别是永赢先锋半导体混合C、国联安优选行业混合、大摩数字经济混合C、德邦鑫星价值灵活配置混合C、东方人工智能主题混合C、东方阿尔法科技智选混合C？"

    async with MemoryManager() as memory, TraceLogger() as trace:
        result = await run_chat(
            session_id, user_message, memory, trace,
            on_chunk=None,  # CLI print 模式
        )
        # 分类日志
        category = result["category"]
        print(f"\n[测试完成 category={category}]")

asyncio.run(main())
