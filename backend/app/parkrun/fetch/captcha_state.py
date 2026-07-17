from __future__ import annotations

import time

from app.config import get_settings
from app.core.redis_client import get_redis_client

CAPTCHA_PENDING_KEY = "parkrun:captcha_pending"
CAPTCHA_PENDING_MESSAGE_KEY = "parkrun:captcha_pending_message"
CAPTCHA_PENDING_TTL_SECONDS = 7 * 24 * 3600

BAN_COOLDOWN_KEY = "parkrun:fetch:ban_cooldown_until"
BAN_LEVEL_KEY = "parkrun:fetch:ban_level"
# Уровень эскалации должен пережить своё охлаждение, иначе проба после самой
# длинной ступени начинала бы лестницу заново вместо повторения ступени.
_BAN_LEVEL_EXTRA_TTL_SECONDS = 7 * 24 * 3600


def _cooldown_steps() -> list[int]:
    raw = get_settings().parkrun_ban_cooldown_steps_seconds
    steps = [int(part) for part in raw.split(",") if part.strip()]
    return steps or [3600]


def escalate_ban_cooldown() -> float:
    """Поднять уровень охлаждения на ступень (1ч → 5ч → 24ч → 72ч → неделя).

    Возвращает unix-время окончания охлаждения. Вызывается при каждой
    обнаруженной капче/бане; успешный фетч сбрасывает лестницу через
    clear_captcha_pending().
    """
    redis = get_redis_client()
    steps = _cooldown_steps()
    level = int(redis.incr(BAN_LEVEL_KEY))
    step = steps[min(level, len(steps)) - 1]
    until = time.time() + step
    redis.expire(BAN_LEVEL_KEY, step + _BAN_LEVEL_EXTRA_TTL_SECONDS)
    redis.set(BAN_COOLDOWN_KEY, str(until), ex=step + 60)
    return until


def ban_cooldown_level() -> int:
    raw = get_redis_client().get(BAN_LEVEL_KEY)
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def set_captcha_pending(message: str) -> None:
    redis = get_redis_client()
    redis.set(CAPTCHA_PENDING_KEY, "1", ex=CAPTCHA_PENDING_TTL_SECONDS)
    redis.set(CAPTCHA_PENDING_MESSAGE_KEY, message, ex=CAPTCHA_PENDING_TTL_SECONDS)


def clear_captcha_pending() -> None:
    """Полный сброс после успешного фетча: капча пройдена, лестница обнуляется."""
    redis = get_redis_client()
    redis.delete(CAPTCHA_PENDING_KEY, CAPTCHA_PENDING_MESSAGE_KEY, BAN_COOLDOWN_KEY, BAN_LEVEL_KEY)


def is_captcha_pending() -> bool:
    return get_redis_client().get(CAPTCHA_PENDING_KEY) is not None


def captcha_pending_message() -> str | None:
    raw = get_redis_client().get(CAPTCHA_PENDING_MESSAGE_KEY)
    return raw if raw else None


def platform_cooldown_remaining() -> int | None:
    from app.platform_fetch.cooldown import platform_cooldown_until

    until = platform_cooldown_until("parkrun")
    if until is None:
        return None
    remaining = round(until - time.time())
    return remaining if remaining > 0 else None
