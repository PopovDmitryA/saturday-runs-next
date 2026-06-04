from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from app.core.redis_client import get_redis_client

_COOLDOWN_KEYS = {
    "parkrun": "parkrun:fetch:ban_cooldown_until",
    "s95": "s95:fetch:ban_cooldown_until",
    "five_verst": "five_verst:fetch:ban_cooldown_until",
}

_UNTIL_RE = re.compile(r"until\s+(\d+(?:\.\d+)?)", re.IGNORECASE)


def cooldown_key(platform_code: str) -> str | None:
    return _COOLDOWN_KEYS.get(platform_code)


def platform_cooldown_until(platform_code: str) -> float | None:
    key = cooldown_key(platform_code)
    if key is None:
        return None
    raw = get_redis_client().get(key)
    if raw is None:
        return None
    return float(raw)


def is_platform_in_cooldown(platform_code: str) -> bool:
    until = platform_cooldown_until(platform_code)
    return until is not None and time.time() < until


def parse_cooldown_until_from_message(message: str) -> datetime | None:
    match = _UNTIL_RE.search(message)
    if match is None:
        return None
    return datetime.fromtimestamp(float(match.group(1)), tz=timezone.utc)


def clear_platform_cooldown(platform_code: str) -> bool:
    key = cooldown_key(platform_code)
    if key is None:
        return False
    return bool(get_redis_client().delete(key))


def clear_all_platform_cooldowns() -> int:
    redis = get_redis_client()
    deleted = 0
    for key in _COOLDOWN_KEYS.values():
        deleted += int(redis.delete(key))
    return deleted
