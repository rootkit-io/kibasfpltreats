"""Phase 14: Redis caching and rate-limiting unit tests.

Runs against a real Redis when REDIS_URL is set; skips cleanly otherwise.
Tests the helpers in isolation (no FastAPI app needed) so they run fast.
"""

from __future__ import annotations

import asyncio
import os
import time

import asyncio

import pytest

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = bool(os.environ.get("REDIS_URL"))
except ImportError:
    _REDIS_AVAILABLE = False


@pytest.fixture
def redis_url():
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set -- skipping live Redis tests")
    return url


@pytest.fixture
def redis_state(redis_url):
    """Minimal app.state mock with a real Redis pool."""
    from fpl_xpts.redis_utils import open_redis_pool, close_redis_pool

    class State:
        pass

    state = State()
    state.redis_pool = open_redis_pool()
    yield state
    asyncio.run(close_redis_pool(state.redis_pool))


@pytest.fixture
def clean_redis(redis_state):
    """Flush test keys before and after each test."""
    import redis.asyncio as aioredis
    client = aioredis.Redis(connection_pool=redis_state.redis_pool)
    asyncio.run(client.flushdb())
    yield redis_state
    asyncio.run(client.flushdb())


# ---------------------------------------------------------------- cache


def test_cache_miss_returns_none(clean_redis):
    from fpl_xpts.redis_utils import cache_get
    result = asyncio.run(cache_get(clean_redis, "missing:key"))
    assert result is None


def test_cache_set_and_get_roundtrip(clean_redis):
    from fpl_xpts.redis_utils import cache_get, cache_set
    payload = {"run": {"run_id": "abc"}, "count": 2, "player_week": []}
    asyncio.run(cache_set(clean_redis, "test:key", payload, ttl=10))
    result = asyncio.run(cache_get(clean_redis, "test:key"))
    assert result == payload


def test_cache_expires_after_ttl(clean_redis):
    from fpl_xpts.redis_utils import cache_get, cache_set
    asyncio.run(cache_set(clean_redis, "test:expire", {"x": 1}, ttl=1))
    import time; time.sleep(1.1)
    assert asyncio.run(cache_get(clean_redis, "test:expire")) is None


# --------------------------------------------------------- rate limiter


def test_rate_limit_allows_under_limit(clean_redis):
    from fpl_xpts.redis_utils import check_rate_limit, RATE_LIMIT_MAX
    for _ in range(RATE_LIMIT_MAX):
        assert asyncio.run(check_rate_limit(clean_redis, "user_test")) is True


def test_rate_limit_blocks_over_limit(clean_redis):
    from fpl_xpts.redis_utils import check_rate_limit, RATE_LIMIT_MAX
    for _ in range(RATE_LIMIT_MAX):
        asyncio.run(check_rate_limit(clean_redis, "user_over"))
    assert asyncio.run(check_rate_limit(clean_redis, "user_over")) is False


def test_rate_limit_keys_are_per_user(clean_redis):
    from fpl_xpts.redis_utils import check_rate_limit, RATE_LIMIT_MAX
    for _ in range(RATE_LIMIT_MAX):
        asyncio.run(check_rate_limit(clean_redis, "user_a"))
    # user_b is unaffected
    assert asyncio.run(check_rate_limit(clean_redis, "user_b")) is True


# --------------------------------------------------------- no-redis fallback


def test_cache_get_returns_none_without_redis():
    from fpl_xpts.redis_utils import cache_get

    class NoRedis:
        redis_pool = None

    assert asyncio.run(cache_get(NoRedis(), "key")) is None


def test_rate_limit_allows_without_redis():
    from fpl_xpts.redis_utils import check_rate_limit

    class NoRedis:
        redis_pool = None

    assert asyncio.run(check_rate_limit(NoRedis(), "user")) is True
