"""滑动窗口限流器 —— Redis Sorted Set 实现，admin 跳过。

用法:
    rate_limiter = RateLimiter()
    await rate_limiter.connect()       # lifespan startup
    ...
    token_info = await check_rate_limit(token_info, rate_limiter)  # Depends
    await rate_limiter.disconnect()    # lifespan shutdown
"""

import time
import asyncio

import redis.asyncio as aioredis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD,
    RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS,
)
from core.auth import TokenInfo

RETRY_MAX = 3
RETRY_DELAY = 1.0


class RateLimiter:
    """滑动窗口限流器。

    算法: ZREMRANGEBYSCORE + ZCARD + ZADD 管道执行（3 条命令原子化）。
    Redis 不可用时降级放行。
    """

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
                    print("[RateLimit] Redis 不可用，限流降级为放行")

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @property
    def _connected(self) -> bool:
        return self._redis is not None

    # ── 限流检查 ──────────────────────────────────────────────

    async def check(self, token_info: TokenInfo) -> tuple[bool, int]:
        """检查是否放行。返回 (放行?, 剩余窗口秒数)。

        admin 直接放行，不消耗 Redis 配额。
        """
        if token_info.tier == "admin":
            return True, 0

        if not self._connected:
            return True, 0

        now_ms = int(time.time() * 1000)
        window_ms = RATE_LIMIT_WINDOW_SECONDS * 1000
        cutoff_ms = now_ms - window_ms
        key = f"ratelimit:{token_info.token}"

        try:
            async with self._redis.pipeline() as pipe:
                pipe.zremrangebyscore(key, 0, cutoff_ms)
                pipe.zcard(key)
                pipe.zadd(key, {str(now_ms): now_ms})
                pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS * 2)  # 窗口2倍TTL兜底
                _, count, _, _ = await pipe.execute()

            if count >= RATE_LIMIT_MAX_REQUESTS:
                # 计算 Retry-After：取窗口中最早记录的时间
                oldest_raw = await self._redis.zrange(key, 0, 0, withscores=True)
                if oldest_raw:
                    oldest_ms = oldest_raw[0][1]
                    retry_after = int((oldest_ms + window_ms - now_ms) / 1000)
                    return False, max(1, retry_after)
                return False, RATE_LIMIT_WINDOW_SECONDS

            return True, 0
        except Exception:
            self._redis = None
            return True, 0
