"""Redis connection pool and helpers for caching + rate limiting (Phase 14).

Lifecycle: the pool is opened in the FastAPI lifespan and stored on
``app.state.redis``. Both the cache helper and the rate-limiter key function
read it from there -- no module-level globals, no import-time connections.

Graceful degradation: every public function catches ``RedisError`` and falls
back silently (cache miss / rate-limit skip). Redis being down must never
take the API down.
"""

from __future__ import annotations

import json
import os
from typing import Any

REDIS_URL_ENV = "REDIS_URL"
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# Cache TTL for the published projections response (seconds).
PROJECTIONS_CACHE_TTL = 60

# Rate-limit window and max requests per user per window.
RATE_LIMIT_WINDOW = 60   # seconds
RATE_LIMIT_MAX = 30      # requests per window


def get_redis_url() -> str:
    return os.environ.get(REDIS_URL_ENV, _DEFAULT_REDIS_URL)


def open_redis_pool():
    """Return a redis.asyncio.ConnectionPool, or None when redis is absent."""
    try:
        import redis.asyncio as aioredis
        return aioredis.ConnectionPool.from_url(
            get_redis_url(),
            max_connections=10,
            decode_responses=True,
        )
    except Exception:  # pragma: no cover - redis not installed
        return None


async def close_redis_pool(pool) -> None:
    if pool is None:
        return
    try:
        await pool.disconnect()
    except Exception:
        pass


def _client(app_state):
    """Return a redis.asyncio.Redis client from the pool on app.state, or None."""
    pool = getattr(app_state, "redis_pool", None)
    if pool is None:
        return None
    try:
        import redis.asyncio as aioredis
        return aioredis.Redis(connection_pool=pool)
    except Exception:
        return None


async def cache_get(app_state, key: str) -> Any | None:
    """Return the cached value for ``key``, or None on miss/error."""
    client = _client(app_state)
    if client is None:
        return None
    try:
        raw = await client.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception:
        return None


async def cache_set(app_state, key: str, value: Any, ttl: int = PROJECTIONS_CACHE_TTL) -> None:
    """Store ``value`` as JSON under ``key`` with ``ttl`` seconds expiry."""
    client = _client(app_state)
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        pass


async def check_rate_limit(app_state, user_id: str) -> bool:
    """Sliding-window rate limiter keyed by Clerk ``sub`` (user_id).

    Returns True if the request is allowed, False if the limit is exceeded.
    Falls back to True (allow) when Redis is unavailable.
    """
    client = _client(app_state)
    if client is None:
        return True  # degrade gracefully: no Redis -> no rate limiting
    try:
        import time
        key = f"rl:projections:{user_id}"
        now = int(time.time())
        window_start = now - RATE_LIMIT_WINDOW

        pipe = client.pipeline()
        # Remove counts outside the current window, add this request, count.
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, RATE_LIMIT_WINDOW + 1)
        results = await pipe.execute()
        count = results[2]
        return count <= RATE_LIMIT_MAX
    except Exception:
        return True  # degrade gracefully
