"""会话记忆层 —— 基于 Redis 的短期对话上下文管理。

Key 结构：
    session:{id}:messages  →  List（用户 ↔ 助手对话，LTRIM 保持最近 10 条）
    session:{id}:meta      →  Hash（创建时间、最后活跃时间）

Redis 不可用时自动降级为内存 dict。
"""

import json
import asyncio
from datetime import datetime, timezone

import redis.asyncio as aioredis

from config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD


MAX_MESSAGES = 10          # 保留最近 10 条消息（5 轮对话）
TTL_SECONDS = 1800         # 30 分钟无操作过期
RETRY_MAX = 3              # 连接重试次数
RETRY_DELAY = 1.0          # 重试间隔（秒）

class MemoryManager:
    """会话短期记忆管理器。

    用法 —— CLI（async with 自动管理连接）:
        async with MemoryManager() as mem:
            history = await mem.load_messages(sid)
            await mem.append_message(sid, msg)

    用法 —— FastAPI（手动生命周期）:
        mem = MemoryManager()
        await mem.connect()       # startup 事件
        ...
        await mem.disconnect()    # shutdown 事件
    """

    def __init__(self):
        self._redis: aioredis.Redis | None = None
        self._fallback: dict[str, list[dict]] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 Redis 连接。失败则启用内存降级，不抛异常。"""
        url = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
        kwargs = {"decode_responses": False}
        if REDIS_PASSWORD:
            kwargs["password"] = REDIS_PASSWORD

        for i in range(RETRY_MAX):
            try:
                self._redis = aioredis.from_url(url, **kwargs)
                await self._redis.ping()
                return
            except Exception:
                if i < RETRY_MAX - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    self._redis = None  # 最终失败 → 降级
                    print("[Memory] Redis 不可用，已降级为内存模式（数据不持久化）")

    async def disconnect(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def __aenter__(self) -> "MemoryManager":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()

    # ── 内部 ──────────────────────────────────────────────────

    @property
    def _connected(self) -> bool:
        return self._redis is not None

    @staticmethod
    def _msg_key(session_id: str) -> str:
        return f"session:{session_id}:messages"

    @staticmethod
    def _meta_key(session_id: str) -> str:
        return f"session:{session_id}:meta"

    @staticmethod
    def _summary_flag_key(session_id: str) -> str:
        return f"session:{session_id}:needs_summary"

    # ── 摘要标记 ──────────────────────────────────────────────

    async def set_summary_flag(self, session_id: str) -> None:
        """N 轮结束后打标记：下次请求需要先摘要再加载历史。"""
        if self._connected:
            key = self._summary_flag_key(session_id)
            try:
                await self._redis.set(key, "1", ex=TTL_SECONDS)
            except Exception:
                self._redis = None

    async def check_and_clear_summary_flag(self, session_id: str) -> bool:
        """N+1 轮开始时检查标记。GETDEL 原子操作：读取并删除，只有一人能拿到 True。

        返回 True 表示需要先执行摘要再加载历史。
        """
        if not self._connected:
            return False
        key = self._summary_flag_key(session_id)
        try:
            result = await self._redis.getdel(key)
            return result is not None
        except Exception:
            self._redis = None
            return False

    # ── 消息读写 ──────────────────────────────────────────────

    async def load_messages(self, session_id: str) -> list[dict]:
        """加载该会话的历史消息（最多 MAX_MESSAGES 条）。"""
        if not self._connected:
            return self._fallback.get(session_id, []).copy()

        key = self._msg_key(session_id)
        raw = await self._redis.lrange(key, 0, -1)
        return [json.loads(m) for m in raw]

    async def append_message(self, session_id: str, msg: dict) -> None:
        """追加一条消息，自动裁剪窗口 + 刷新 TTL + 更新元数据。"""
        msg.setdefault("_v", 1)
        data = json.dumps(msg, ensure_ascii=False)

        if self._connected:
            msg_key = self._msg_key(session_id)
            meta_key = self._meta_key(session_id)
            now = datetime.now(timezone.utc).isoformat()

            async with self._redis.pipeline() as pipe:
                pipe.rpush(msg_key, data)
                pipe.ltrim(msg_key, -MAX_MESSAGES, -1)
                pipe.expire(msg_key, TTL_SECONDS)
                pipe.hset(meta_key, mapping={"last_active": now})
                pipe.expire(meta_key, TTL_SECONDS)
                await pipe.execute()
        else:
            buf = self._fallback.setdefault(session_id, [])
            buf.append(msg)
            self._fallback[session_id] = buf[-MAX_MESSAGES:]

    # ── 摘要化 ────────────────────────────────────────────────

    async def _overwrite_messages(self, session_id: str, messages: list[dict]) -> None:
        """用新消息列表覆盖 Redis / fallback 中的历史记录。"""
        if self._connected:
            key = self._msg_key(session_id)
            async with self._redis.pipeline() as pipe:
                pipe.delete(key)
                for m in messages:
                    pipe.rpush(key, json.dumps(m, ensure_ascii=False))
                pipe.expire(key, TTL_SECONDS)
                await pipe.execute()
        else:
            self._fallback[session_id] = messages

    # ── 元数据 ────────────────────────────────────────────────

    async def get_meta(self, session_id: str) -> dict:
        """获取会话元数据（创建时间、最后活跃时间等）。"""
        if not self._connected:
            return {}
        meta = await self._redis.hgetall(self._meta_key(session_id))
        return {k.decode(): v.decode() for k, v in meta.items()}

    async def delete_session(self, session_id: str) -> None:
        """删除整个会话的消息和元数据。"""
        if self._connected:
            await self._redis.delete(
                self._msg_key(session_id),
                self._meta_key(session_id),
            )
        else:
            self._fallback.pop(session_id, None)
