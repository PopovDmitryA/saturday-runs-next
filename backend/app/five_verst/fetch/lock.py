from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.five_verst.errors import FiveVerstFetchTimeout

LOCK_KEY = "five_verst:fetch:global_lock"


@contextmanager
def five_verst_fetch_lock() -> Iterator[None]:
    """Distributed lock: only one 5verst fetch at a time cluster-wide."""
    settings = get_settings()
    redis = get_redis_client()
    token = str(uuid.uuid4())
    timeout = settings.five_verst_fetch_lock_timeout_seconds
    blocking_timeout = settings.five_verst_fetch_lock_blocking_seconds
    deadline = time.time() + blocking_timeout

    acquired = False
    while time.time() < deadline:
        if redis.set(LOCK_KEY, token, nx=True, ex=timeout):
            acquired = True
            break
        time.sleep(1.0)

    if not acquired:
        raise FiveVerstFetchTimeout("Timed out waiting for 5verst fetch lock")

    try:
        yield
    finally:
        current = redis.get(LOCK_KEY)
        if current == token:
            redis.delete(LOCK_KEY)
