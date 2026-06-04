from __future__ import annotations

from app.core.redis_client import get_redis_client


def get_redis():
    return get_redis_client()


def increment_counter(key: str, window_seconds: int) -> int:
    client = get_redis()
    count = client.incr(key)
    if count == 1:
        client.expire(key, window_seconds)
    return int(count)


def get_counter_ttl(key: str) -> int:
    ttl = get_redis().ttl(key)
    return int(ttl) if ttl and ttl > 0 else 0


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if request is allowed, False if rate limited."""
    count = increment_counter(key, window_seconds)
    return count <= limit
