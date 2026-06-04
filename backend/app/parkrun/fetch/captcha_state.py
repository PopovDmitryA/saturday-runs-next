from __future__ import annotations

import time

from app.core.redis_client import get_redis_client

CAPTCHA_PENDING_KEY = "parkrun:captcha_pending"
CAPTCHA_PENDING_MESSAGE_KEY = "parkrun:captcha_pending_message"
CAPTCHA_PENDING_TTL_SECONDS = 7 * 24 * 3600


def set_captcha_pending(message: str) -> None:
    redis = get_redis_client()
    redis.set(CAPTCHA_PENDING_KEY, "1", ex=CAPTCHA_PENDING_TTL_SECONDS)
    redis.set(CAPTCHA_PENDING_MESSAGE_KEY, message, ex=CAPTCHA_PENDING_TTL_SECONDS)


def clear_captcha_pending() -> None:
    redis = get_redis_client()
    redis.delete(CAPTCHA_PENDING_KEY, CAPTCHA_PENDING_MESSAGE_KEY)


def is_captcha_pending() -> bool:
    return get_redis_client().get(CAPTCHA_PENDING_KEY) is not None


def captcha_pending_message() -> str | None:
    raw = get_redis_client().get(CAPTCHA_PENDING_MESSAGE_KEY)
    return raw if raw else None


def platform_cooldown_remaining() -> float | None:
    from app.platform_fetch.cooldown import platform_cooldown_until

    until = platform_cooldown_until("parkrun")
    if until is None:
        return None
    remaining = until - time.time()
    return remaining if remaining > 0 else None
