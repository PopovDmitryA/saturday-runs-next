from __future__ import annotations

import random
import time

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.core.request_cancel import check_cancelled, interruptible_sleep

LAST_FETCH_KEY = "parkrun:fetch:last_completed_at"


def wait_for_turn(*, reason: str) -> None:
    del reason
    settings = get_settings()
    redis = get_redis_client()
    min_interval = settings.parkrun_fetch_min_interval_seconds
    max_interval = settings.parkrun_fetch_max_interval_seconds
    target_interval = random.uniform(min_interval, max_interval)

    while True:
        check_cancelled()
        raw = redis.get(LAST_FETCH_KEY)
        if raw is None:
            return
        last_at = float(raw)
        elapsed = time.time() - last_at
        if elapsed >= target_interval:
            return
        interruptible_sleep(min(target_interval - elapsed, 5.0))


def mark_fetch_completed() -> None:
    redis = get_redis_client()
    redis.set(LAST_FETCH_KEY, str(time.time()))
