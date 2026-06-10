from functools import lru_cache

import redis

_test_redis_override: redis.Redis | None = None


@lru_cache
def _production_redis_client() -> redis.Redis:
    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def get_redis_client() -> redis.Redis:
    if _test_redis_override is not None:
        return _test_redis_override
    return _production_redis_client()
