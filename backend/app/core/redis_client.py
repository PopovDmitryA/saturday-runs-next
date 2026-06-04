from functools import lru_cache

import redis


@lru_cache
def get_redis_client() -> redis.Redis:
    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True)
