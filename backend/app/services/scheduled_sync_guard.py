from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

_MOSCOW = ZoneInfo("Europe/Moscow")
_SLOT_TTL_SECONDS = 3 * 3600
# Суббота и воскресенье по Москве: время свежих протоколов. Фоновые цепочки
# (сверка, обход недели) в него не заезжают — 19.09.2026 пятничная цепочка
# сверки доедала 2023 год до субботнего полудня, и latest за весь день так и
# не забрали: в очереди лежало 22 протухших запуска.
_FRESHNESS_WEEKDAYS = {5, 6}
# Звено цепочки живёт два часа: дольше ждать его некому, а протухшее звено
# умирает молча, вместо того чтобы копиться в очереди.
_CHAIN_LINK_TTL_SECONDS = 2 * 3600
# Метка «цепочка идёт» переживает один кусок фона с запасом и продлевается
# каждым звеном: если воркер умрёт на середине, следующий запуск по
# расписанию дождётся протухания и начнёт заново.
_CHAIN_TTL_SECONDS = 3 * 3600


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


def _moscow(now: datetime | None = None) -> datetime:
    ts = now or datetime.now(_MOSCOW)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=_MOSCOW)
    return ts.astimezone(_MOSCOW)


def is_freshness_window(now: datetime | None = None) -> bool:
    """Выходные по Москве: воркер занят свежими протоколами, фону здесь не место."""

    return _moscow(now).weekday() in _FRESHNESS_WEEKDAYS


def chain_link_expires(now: datetime | None = None) -> datetime:
    """Момент, после которого звено фоновой цепочки не нужно запускать вовсе.

    Два часа — потолок ожидания; ближайшие выходные — стена: звено, не взятое до
    субботы, отменяется, а не встаёт поперёк субботних протоколов.
    """

    ts = _moscow(now)
    deadline = ts + timedelta(seconds=_CHAIN_LINK_TTL_SECONDS)
    days_to_saturday = (5 - ts.weekday()) % 7
    weekend_start = (ts + timedelta(days=days_to_saturday)).replace(hour=0, minute=0, second=0, microsecond=0)
    if weekend_start <= ts:
        weekend_start = ts
    return min(deadline, weekend_start).astimezone(timezone.utc)


def _chain_key(pipeline_key: str) -> str:
    return f"sync-chain:{pipeline_key}"


def try_start_background_chain(pipeline_key: str, *, force: bool = False) -> bool:
    """Одна живая цепочка на пайплайн: вторая начнётся, когда закончится первая.

    Часовой слот от этого не спасает: сверка запускается раз в три часа, а
    цепочка при раздутом размере пачки идёт дольше — 19.09.2026 на проде их
    накопилось три штуки разом, и воркер не вылезал из фона неделями.
    """

    redis = get_redis_client()
    key = _chain_key(pipeline_key)
    if force:
        redis.set(key, "1", ex=_CHAIN_TTL_SECONDS)
        return True
    started = bool(redis.set(key, "1", nx=True, ex=_CHAIN_TTL_SECONDS))
    if not started:
        logger.info("Skip background chain for %s: previous one is still running", pipeline_key)
    return started


def keep_background_chain_alive(pipeline_key: str) -> None:
    get_redis_client().set(_chain_key(pipeline_key), "1", ex=_CHAIN_TTL_SECONDS)


def finish_background_chain(pipeline_key: str) -> None:
    get_redis_client().delete(_chain_key(pipeline_key))
