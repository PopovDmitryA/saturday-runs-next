from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from collections.abc import Iterator

from app.config import get_settings
from app.core.redis_client import get_redis_client

LOCK_KEY = "s95:fetch:global_lock"


@contextmanager
def s95_fetch_lock() -> Iterator[None]:
    """Distributed lock: only one S95 fetch (browser) at a time cluster-wide."""
    settings = get_settings()
    redis = get_redis_client()
    token = str(uuid.uuid4())
    timeout = settings.s95_fetch_lock_timeout_seconds
    blocking_timeout = settings.s95_fetch_lock_blocking_seconds
    deadline = time.time() + blocking_timeout

    acquired = False
    while time.time() < deadline:
        if redis.set(LOCK_KEY, token, nx=True, ex=timeout):
            acquired = True
            break
        time.sleep(1.0)

    if not acquired:
        from app.debug_agent_log import agent_log
        from app.s95.errors import S95FetchTimeout

        agent_log(
            location="lock.py:s95_fetch_lock:timeout",
            message="S95 fetch lock timeout",
            data={"blocking_timeout": blocking_timeout},
            hypothesis_id="B",
        )
        raise S95FetchTimeout("Timed out waiting for S95 fetch lock")

    try:
        yield
    finally:
        current = redis.get(LOCK_KEY)
        if current == token:
            redis.delete(LOCK_KEY)
