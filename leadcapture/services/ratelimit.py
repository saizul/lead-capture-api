"""Rate limiting with an in-memory sliding window and an optional Redis backend."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("leadcapture.ratelimit")


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window: int) -> RateLimitResult: ...

    async def close(self) -> None: ...


class MemoryRateLimiter:
    """Sliding-window log. Per-process: use Redis when running several workers."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._ops = 0

    async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = self._clock()
        bucket = self._hits.setdefault(key, deque())
        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        self._ops += 1
        if self._ops % 1000 == 0:
            self._prune(cutoff)
        if len(bucket) >= limit:
            retry = max(1, math.ceil(bucket[0] + window - now))
            return RateLimitResult(False, limit, 0, retry)
        bucket.append(now)
        return RateLimitResult(True, limit, limit - len(bucket), 0)

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, b in self._hits.items() if not b or b[-1] <= cutoff]:
            del self._hits[key]

    async def close(self) -> None:
        self._hits.clear()


class RedisRateLimiter:
    """Fixed-window counter shared across processes. Fails open if Redis is unavailable."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.time) -> None:
        self._redis = client
        self._clock = clock

    async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = self._clock()
        bucket = int(now // window)
        redis_key = f"lc:rl:{key}:{bucket}"
        retry_after = max(1, int(window - (now % window)))
        try:
            count = int(await self._redis.incr(redis_key))
            if count == 1:
                await self._redis.expire(redis_key, window + 1)
        except Exception:
            logger.warning("redis rate limiter unavailable, failing open", exc_info=True)
            return RateLimitResult(True, limit, limit, 0)
        if count > limit:
            return RateLimitResult(False, limit, 0, retry_after)
        return RateLimitResult(True, limit, limit - count, 0)

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # pragma: no cover - best effort
            logger.debug("error closing redis", exc_info=True)


def build_rate_limiter(redis_url: str | None) -> RateLimiter:
    if not redis_url:
        return MemoryRateLimiter()
    try:
        import redis.asyncio as redis_asyncio
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "REDIS_URL is set but the 'redis' package is missing. "
            "Install with: pip install 'lead-capture-api[redis]'"
        ) from exc
    return RedisRateLimiter(redis_asyncio.from_url(redis_url, decode_responses=True))
