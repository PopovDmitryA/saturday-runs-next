"""Гости локации: сколько на старте было людей, чей дом — другая площадка.

Заявки из бэклога сайта («"Туристическая" статистика локации» и «В протокол
локации добавить количество "туристов"») просят одно число: сколько народу
приезжает к нам со стороны.

**Почему «гости», а не «туристы».** Так это уже называется в постах кабинета
организатора — рубрика «🧳 Гости локации» (`organizer_post_service`,
формулировка Дмитрия 24.08.2026): гость — финишёр, чья домашняя локация
другая, и гостить он может не в первый раз. Дом берём тем же
`participant_home_keys`, что и пост, — иначе на одном сайте было бы два разных
ответа на вопрос «кто здесь свой».

**Чем отличается от «Впервые здесь».** Соседняя метрика (`is_first_run_at_location`,
см. `newcomer_counts`) считает только ПЕРВЫЙ визит человека на площадку. Гостей
всегда больше: в них попадают и те, кто ездит сюда регулярно, но живёт в другом
парке. Одно множество вложено в другое, поэтому в интерфейсе они стоят рядом
и подписаны так, чтобы это было видно.

**Что число не умеет.** Дом считается по истории НА СЕГОДНЯ, а не на дату
старта: человек, переехавший в наш парк, задним числом перестаёт быть гостем на
своих старых визитах. Участники, у которых дом ещё не определился (первые
старты, мало данных), в гости не попадают — как и в посте.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Event, EventCrosslink, RunResult

if TYPE_CHECKING:  # сама идентичность приходит из location_page_service, а он
    # импортирует этот модуль — ссылку держим только для типов.
    from app.services.location_page_service import LocationIdentity

# Полный расчёт по крупной площадке — несколько секунд, и умножать это на 270
# локаций в каждом прогреве незачем: числу нужен один новый старт в неделю.
# Поэтому ключ живёт неделю и ДОПОЛНЯЕТСЯ (см. location_guests_by_event), а
# полный пересчёт случается раз за TTL — он же подтягивает сместившиеся дома.
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def _cache_key(identity_key: str) -> str:
    return f"location:guests:v1:{identity_key}"


def _read_cache(identity_key: str) -> dict[str, int] | None:
    try:
        raw = get_redis_client().get(_cache_key(identity_key))
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return {str(key): int(value) for key, value in json.loads(raw).items()}
    except (AttributeError, TypeError, ValueError):
        return None


def _write_cache(identity_key: str, by_event: dict[UUID, int]) -> None:
    try:
        get_redis_client().setex(
            _cache_key(identity_key),
            _CACHE_TTL_SECONDS,
            json.dumps({str(event_id): count for event_id, count in by_event.items()}),
        )
    except redis.RedisError:
        pass


def _our_event_ids(identity: LocationIdentity):
    """Подзапрос: события площадки без тестовых и без кросслинкованных дублей."""
    return select(Event.id).where(
        Event.location_id.in_([location.id for location, _code in identity.locations]),
        Event.is_test_event.is_(False),
        Event.id.notin_(select(EventCrosslink.secondary_event_id)),
    )


def location_guests_by_event(
    db: Session, identity: LocationIdentity, *, refresh: bool = False
) -> dict[UUID, int]:
    """Сколько гостей финишировало на каждом старте площадки: event_id → число.

    Кэш ДОПОЛНЯЕТСЯ, а не пересчитывается целиком: старты прошлых лет свои
    числа не меняют, а новая суббота добавляет один старт на сотню финишёров —
    это доли секунды против нескольких секунд на полный проход по истории.

    refresh=True — проверить, не появилось ли новых стартов (так ходит прогрев
    страниц локаций). Полный пересчёт случается, когда ключ протух: он же
    подтягивает участников, у которых с тех пор сменился дом.
    """
    cached = _read_cache(identity.identity_key)
    if cached is None:
        by_event = _compute_guests_by_event(db, identity)
        _write_cache(identity.identity_key, by_event)
        return by_event

    by_event = {UUID(event_id): count for event_id, count in cached.items()}
    if not refresh:
        return by_event

    known = set(by_event)
    fresh = [
        event_id
        for (event_id,) in db.query(Event.id).filter(Event.id.in_(_our_event_ids(identity))).all()
        if event_id not in known
    ]
    if not fresh:
        return by_event
    by_event.update(_compute_guests_by_event(db, identity, only_events=fresh))
    _write_cache(identity.identity_key, by_event)
    return by_event


def cached_guests_by_event(identity_key: str) -> dict[UUID, int] | None:
    """Готовые числа из кэша, БЕЗ расчёта. None — кэш холодный.

    Нужно витринам, которые показывают сразу все локации («Последние
    пробежки»): посчитать гостей для 250 площадок в одном запросе нельзя, а
    прогрев (locations.warm_cache) наполняет эти ключи каждые два часа.
    """
    cached = _read_cache(identity_key)
    if cached is None:
        return None
    return {UUID(event_id): count for event_id, count in cached.items()}


def _compute_guests_by_event(
    db: Session, identity: LocationIdentity, *, only_events: list[UUID] | None = None
) -> dict[UUID, int]:
    """only_events — считать не всю историю, а перечисленные старты."""
    if not identity.locations:
        return {}

    event_filter = (
        RunResult.event_id.in_(only_events)
        if only_events is not None
        else RunResult.event_id.in_(_our_event_ids(identity))
    )
    finisher_rows = (
        db.query(RunResult.event_id, RunResult.participant_id)
        .filter(
            event_filter,
            RunResult.participant_id.isnot(None),
            # Финишёры считаются по времени финиша — см. run_results data quirks.
            RunResult.finish_time_sec.isnot(None),
        )
        .all()
    )
    if not finisher_rows:
        return {}

    # Общесайтовая логика дома — та же функция, что у рубрики «Гости локации»
    # в посте организатора.
    from app.services.organizer_service import participant_home_keys

    homes = participant_home_keys(db, {pid for _event_id, pid in finisher_rows})

    guests: dict[UUID, int] = defaultdict(int)
    for event_id, participant_id in finisher_rows:
        home = homes.get(participant_id)
        # Дом не определился — человек не «свой» и не гость: молчим, как пост.
        guests[event_id] += 1 if home is not None and home != identity.identity_key else 0
    return dict(guests)


def location_guests_summary(
    db: Session, identity: LocationIdentity, *, refresh: bool = False
) -> tuple[dict[UUID, int], dict[str, object]]:
    """Числа по стартам плюс сводка для страницы локации."""
    by_event = location_guests_by_event(db, identity, refresh=refresh)
    events_with_protocol = len(by_event)
    total = sum(by_event.values())
    finishers_total = (
        db.query(func.count(RunResult.id))
        .filter(
            RunResult.event_id.in_(_our_event_ids(identity)),
            RunResult.participant_id.isnot(None),
            RunResult.finish_time_sec.isnot(None),
        )
        .scalar()
        or 0
    )
    summary: dict[str, object] = {
        "total": total,
        "share_pct": round(total / finishers_total * 100, 1) if finishers_total else None,
        "avg_per_event": round(total / events_with_protocol, 1) if events_with_protocol else None,
    }
    return by_event, summary
