from __future__ import annotations

import time

from app.config import get_settings
from app.core.redis_client import get_redis_client

# Ключ читает и app/platform_fetch/cooldown.py — общий для всех платформ реестр
# охлаждений; менять имя только вместе с ним.
BAN_COOLDOWN_KEY = "s95:fetch:ban_cooldown_until"
BAN_LEVEL_KEY = "s95:fetch:ban_level"
# Уровень должен пережить своё охлаждение: иначе проба после самой длинной
# ступени начинала бы лестницу заново вместо повторения ступени.
_BAN_LEVEL_EXTRA_TTL_SECONDS = 7 * 24 * 3600


def _cooldown_steps() -> list[int]:
    raw = get_settings().s95_ban_cooldown_steps_seconds
    steps = [int(part) for part in raw.split(",") if part.strip()]
    return steps or [3600]


def escalate_ban_cooldown() -> float:
    """Поднять охлаждение на ступень (1ч → 5ч → 24ч → 72ч → неделя).

    Возвращает unix-время окончания. Вызывается на каждый отказ s95 — и на 403 с
    429, и на разрыв соединения. Успешный запрос сбрасывает лестницу через
    clear_ban_cooldown().
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
    except (TypeError, ValueError):
        return 0


def clear_ban_cooldown() -> None:
    """Сброс после успешного запроса: дверь открыта, лестница обнуляется."""
    get_redis_client().delete(BAN_COOLDOWN_KEY, BAN_LEVEL_KEY)


def ban_cooldown_until() -> float | None:
    raw = get_redis_client().get(BAN_COOLDOWN_KEY)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
