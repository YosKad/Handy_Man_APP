"""Token-bucket rate limiting.

Buckets are declared once here, matching ``docs/04-api-design.md`` §10, so the
limits are reviewable in one place instead of scattered across routes.

Two implementations behind one protocol: Redis for real deployments, in-memory
for tests. Rate limiting is *never* silently disabled — a Redis outage is
treated as a limiter failure and fails open with a warning, because refusing all
traffic because the limiter is down is worse than briefly unmetered traffic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger

logger = get_logger(__name__)


class Bucket(StrEnum):
    AUTH = "auth"
    UPLOAD = "upload"
    ANALYSIS = "analysis"
    GUIDE = "guide"
    CHAT = "chat"
    READ = "read"


@dataclass(frozen=True, slots=True)
class Limit:
    capacity: int
    window_seconds: int

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.window_seconds


LIMITS: dict[Bucket, Limit] = {
    Bucket.AUTH: Limit(capacity=10, window_seconds=15 * 60),
    Bucket.UPLOAD: Limit(capacity=60, window_seconds=60 * 60),
    Bucket.ANALYSIS: Limit(capacity=20, window_seconds=60 * 60),
    Bucket.GUIDE: Limit(capacity=30, window_seconds=60 * 60),
    Bucket.CHAT: Limit(capacity=60, window_seconds=60 * 60),
    Bucket.READ: Limit(capacity=600, window_seconds=5 * 60),
}


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiterPort(Protocol):
    async def check(self, bucket: Bucket, identity: str) -> RateLimitResult: ...


# Atomic token bucket. Redis is single-threaded per script, so the read-modify-write
# cannot interleave — doing this in Python would let concurrent requests both pass.
_LUA_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local last = tonumber(state[2])

if tokens == nil then
  tokens = capacity
  last = now
end

local elapsed = math.max(0, now - last)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)

local retry_after = 0
if allowed == 0 then
  retry_after = math.ceil((1 - tokens) / refill_rate)
end

return {allowed, math.floor(tokens), retry_after}
"""


class RedisRateLimiter:
    """Production limiter. Fails open on Redis errors, loudly."""

    def __init__(self, redis: Redis[bytes], *, key_prefix: str = "rl") -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._script = redis.register_script(_LUA_BUCKET_SCRIPT)

    async def check(self, bucket: Bucket, identity: str) -> RateLimitResult:
        limit = LIMITS[bucket]
        key = f"{self._prefix}:{bucket.value}:{identity}"
        try:
            raw = await self._script(
                keys=[key],
                args=[
                    limit.capacity,
                    limit.refill_per_second,
                    time.time(),
                    limit.window_seconds * 2,
                ],
            )
        except RedisError as exc:
            logger.warning(
                "rate limiter unavailable, failing open",
                extra={"bucket": bucket.value, "error": str(exc)},
            )
            return RateLimitResult(allowed=True, remaining=limit.capacity, retry_after_seconds=0)

        allowed, remaining, retry_after = (int(value) for value in raw)
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=remaining,
            retry_after_seconds=retry_after,
        )


class InMemoryRateLimiter:
    """Deterministic limiter for unit tests and single-process local runs.

    Not safe across processes — never use it in a deployment with replicas.
    """

    def __init__(self, *, clock: object | None = None) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._clock = clock

    def _now(self) -> float:
        clock = self._clock
        return clock() if callable(clock) else time.time()

    async def check(self, bucket: Bucket, identity: str) -> RateLimitResult:
        limit = LIMITS[bucket]
        key = f"{bucket.value}:{identity}"
        now = self._now()
        tokens, last = self._state.get(key, (float(limit.capacity), now))

        tokens = min(limit.capacity, tokens + max(0.0, now - last) * limit.refill_per_second)
        if tokens >= 1:
            self._state[key] = (tokens - 1, now)
            return RateLimitResult(allowed=True, remaining=int(tokens - 1), retry_after_seconds=0)

        self._state[key] = (tokens, now)
        retry_after = int((1 - tokens) / limit.refill_per_second) + 1
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)

    def reset(self) -> None:
        self._state.clear()
