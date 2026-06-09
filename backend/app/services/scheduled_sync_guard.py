from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_MOSCOW = ZoneInfo("Europe/Moscow")
_SLOT_TTL_SECONDS = 3 * 3600


def _hour_bucket(now: datetime | None = None) -> str:
    ts = now or datetime.now(_MOSCOW)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_MOSCOW)
    else:
        ts = ts.astimezone(_MOSCOW)
    return ts.strftime("%Y-%m-%dT%H")


def try_claim_hourly_sync_slot(pipeline_key: str, *, force: bool = False) -> bool:
    """One successful scheduled run per pipeline per Moscow hour (unless force=True)."""
    if force:
        return True
    redis = get_redis_client()
    bucket = _hour_bucket()
    key = f"sync-hour-slot:{pipeline_key}:{bucket}"
    claimed = bool(redis.set(key, "1", nx=True, ex=_SLOT_TTL_SECONDS))
    if not claimed:
        logger.info("Skip duplicate scheduled sync for %s (bucket %s)", pipeline_key, bucket)
    return claimed


def release_hourly_sync_slot(pipeline_key: str, *, now: datetime | None = None) -> None:
    """Allow retry in the same hour after a failed run."""
    redis = get_redis_client()
    bucket = _hour_bucket(now)
    key = f"sync-hour-slot:{pipeline_key}:{bucket}"
    redis.delete(key)
