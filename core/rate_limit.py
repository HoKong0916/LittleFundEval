"""滑动窗口限流器 —— Redis Sorted Set 实现。

API 通道按 token（admin 不限流），飞书通道按 open_id。
"""

import asyncio
import logging
import time

import redis.asyncio as aioredis

from config import (
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)
from core.auth import TokenInfo

logger = logging.getLogger(__name__)

RETRY_MAX = 3
RETRY_DELAY = 1.0


class RateLimiter:
    """滑动窗口限流器。ZSET 管道 4 条命令原子执行，Redis 不可用降级放行。"""

    def __init__(self):
        self._redis: aioredis.Redis | None = None

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> None:
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
                    self._redis = None
                    logger.warning("Redis 不可用，RateLimit 已降级为放行")

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @property
    def _connected(self) -> bool:
        return self._redis != None

    # ── 限流检查 ──────────────────────────────────────────────

    async def check(self, token_info: TokenInfo) -> tuple[bool, int]:
        """API 通道：admin 放行，visitor 按 token 限流。"""
        if token_info.tier == "admin":
            return True, 0
        return await self._sliding_window_check(f"lg:ratelimit:token:{token_info.token}")

    async def check_by_user_id(self, user_id: str) -> tuple[bool, int]:
        """飞书通道：按 open_id 限流，key 为 lg:ratelimit:user:{open_id}。"""
        return await self._sliding_window_check(f"lg:ratelimit:user:{user_id}")

    async def _sliding_window_check(self, key: str) -> tuple[bool, int]:
        """滑动窗口核心：ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE 管道执行。"""
        if not self._connected:
            return True, 0

        now_ms = int(time.time() * 1000)
        window_ms = RATE_LIMIT_WINDOW_SECONDS * 1000
        cutoff_ms = now_ms - window_ms           # 滑动窗口左边界

        try:
            # ── 管道原子执行（4 条命令，1 次网络往返）────────────
            # 1. ZREMRANGEBYSCORE → 删除窗口外的旧时间戳
            # 2. ZCARD             → 统计窗口内剩余请求数
            # 3. ZADD              → 插入当前请求时间戳
            # 4. EXPIRE            → 窗口 × 2 TTL，清理长期不活跃的 key
            async with self._redis.pipeline() as pipe:
                pipe.zremrangebyscore(key, 0, cutoff_ms)
                pipe.zcard(key)
                pipe.zadd(key, {str(now_ms): now_ms})
                pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS * 2)
                _, count, _, _ = await pipe.execute()

            # count 是插入前的窗口内请求数（不含当前这条）
            # 如果 >= 阈值，说明加上当前请求就超限
            if count >= RATE_LIMIT_MAX_REQUESTS:
                # 计算 Retry-After：窗口剩余时间 = 最早记录过期时刻 - now
                oldest_raw = await self._redis.zrange(key, 0, 0, withscores=True)
                if oldest_raw:
                    oldest_ms = oldest_raw[0][1]
                    retry_after = int((oldest_ms + window_ms - now_ms) / 1000)
                    return False, max(1, retry_after)
                return False, RATE_LIMIT_WINDOW_SECONDS

            return True, 0
        except Exception:
            # Redis 异常 → 标记不可用，本次放行
            self._redis = None
            return True, 0
