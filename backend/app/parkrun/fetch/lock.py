from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings
from app.core.redis_client import get_redis_client

LOCK_KEY = "parkrun:fetch:global_lock"


@contextmanager
def parkrun_fetch_lock() -> Iterator[None]:
    settings = get_settings()
    redis = get_redis_client()
    token = str(uuid.uuid4())
    timeout = settings.parkrun_fetch_lock_timeout_seconds
    blocking_timeout = settings.parkrun_fetch_lock_blocking_seconds
    deadline = time.time() + blocking_timeout

    acquired = False
    while time.time() < deadline:
        if redis.set(LOCK_KEY, token, nx=True, ex=timeout):
            acquired = True
            break
        time.sleep(1.0)

    if not acquired:
        from app.parkrun.errors import ParkrunFetchTimeout

        raise ParkrunFetchTimeout("Timed out waiting for parkrun fetch lock")

    try:
        yield
    finally:
        current = redis.get(LOCK_KEY)
        if current == token:
            redis.delete(LOCK_KEY)
