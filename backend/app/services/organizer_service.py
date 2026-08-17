"""Инструменты кабинета организатора: «Долгая пауза» и список событий локации.

«Долгая пауза» — перенос одноимённого Grafana-дашборда на данные сайта: кто из
постоянных участников локации давно не появлялся. Постоянство меряется числом
пробежек на локации (порог min_runs), пауза — числом СОБЫТИЙ локации, прошедших
после последнего визита (порог min_missed), а не календарём: локация могла
пропускать субботы.

Отличия от легаси: локация — каноническая идентичность (все платформы точки),
человек склеен по платформам через platform_links (как в топах локации),
без привязки на сайте остаётся платформенный participant отдельной строкой.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventCrosslink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.location_page_service import (
    LocationIdentity,
    _location_event_ids,
    _platform_link_join,
    _read_json_cache,
    _write_json_cache,
)

ABSENCE_CACHE_TTL_SECONDS = 3 * 60 * 60

ABSENCE_MIN_RUNS_DEFAULT = 10
ABSENCE_MIN_MISSED_DEFAULT = 4


def absence_cache_key(identity_key: str, min_runs: int, min_missed: int) -> str:
    return f"organizer:absence:v1:{identity_key}:{min_runs}:{min_missed}"


def build_location_absence(
    db: Session,
    identity: LocationIdentity,
    *,
    min_runs: int = ABSENCE_MIN_RUNS_DEFAULT,
    min_missed: int = ABSENCE_MIN_MISSED_DEFAULT,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = absence_cache_key(identity.identity_key, min_runs, min_missed)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_location_absence(db, identity, min_runs=min_runs, min_missed=min_missed)

    if use_cache:
        _write_json_cache(cache_key, payload, ABSENCE_CACHE_TTL_SECONDS)
    return payload


def _compute_location_absence(
    db: Session, identity: LocationIdentity, *, min_runs: int, min_missed: int
) -> dict[str, Any]:
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)

    base: dict[str, Any] = {
        "location": {"slug": identity.slug, "name": identity.name},
        "min_runs": min_runs,
        "min_missed": min_missed,
        "events_total": len(event_ids),
        "items": [],
        "total": 0,
    }
    if not event_ids:
        return base

    event_dates = sorted(
        {
            row[0]
            for row in db.query(Event.event_date).filter(Event.id.in_(event_ids)).all()
        }
    )

    group_key = func.coalesce(PlatformLink.user_id, RunResult.participant_id)
    display_name = func.max(func.coalesce(User.display_name, Participant.display_name))
    local_rows = (
        db.query(
            group_key.label("group_key"),
            display_name.label("name"),
            func.count(func.distinct(RunResult.event_id)).label("runs_here"),
            func.max(Event.event_date).label("last_date"),
            func.max(User.public_slug).label("slug"),
            func.max(User.serial_id).label("serial_id"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .outerjoin(User, PlatformLink.user_id == User.id)
        .filter(RunResult.event_id.in_(event_ids))
        .group_by(group_key)
        .having(func.count(func.distinct(RunResult.event_id)) >= min_runs)
        .all()
    )
    if not local_rows:
        return base

    # Всего пробежек (по всем локациям) — только для уже отобранных людей.
    # Дедуп RunPark-дублей здесь через NOT IN по кросслинкам: событий много,
    # IN-список всех event_id мира сюда не привезёшь.
    group_keys = [row.group_key for row in local_rows]
    secondary_events = select(EventCrosslink.secondary_event_id)
    total_rows = (
        db.query(
            group_key.label("group_key"),
            func.count(func.distinct(RunResult.event_id)).label("runs_total"),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .outerjoin(PlatformLink, _platform_link_join())
        .filter(
            group_key.in_(group_keys),
            Event.is_test_event.is_(False),
            Event.id.notin_(secondary_events),
        )
        .group_by(group_key)
        .all()
    )
    totals = {row.group_key: int(row.runs_total) for row in total_rows}

    items: list[dict[str, Any]] = []
    for row in local_rows:
        last_date: date = row.last_date
        missed = sum(1 for event_date in event_dates if event_date > last_date)
        if missed < min_missed:
            continue
        items.append(
            {
                "name": row.name,
                "handle": row.slug or (str(row.serial_id) if row.serial_id else None),
                "last_date": last_date.isoformat(),
                "last_date_display": last_date.strftime("%d.%m.%Y"),
                "runs_here": int(row.runs_here),
                "runs_total": totals.get(row.group_key, int(row.runs_here)),
                "missed_events": missed,
            }
        )

    items.sort(key=lambda item: (-int(item["runs_here"]), str(item["name"] or "")))
    base["items"] = items
    base["total"] = len(items)
    return base


def list_identity_event_dates(db: Session, identity: LocationIdentity) -> list[dict[str, Any]]:
    """Даты событий идентичности (новые сверху) для селекта свода.

    Аналог list_report_event_dates из отчёта админки, но по всем платформам
    идентичности и с дедупом кросслинков.
    """
    location_ids = [location.id for location, _code in identity.locations]
    event_ids = _location_event_ids(db, location_ids)
    if not event_ids:
        return []

    finishers = (
        select(func.count())
        .select_from(RunResult)
        .where(RunResult.event_id == Event.id)
        .scalar_subquery()
    )
    rows = (
        db.query(
            Event.id,
            Event.event_date,
            Event.event_number,
            Platform.code,
            finishers.label("finishers_count"),
        )
        .join(Platform, Event.platform_id == Platform.id)
        .filter(Event.id.in_(event_ids))
        .order_by(Event.event_date.desc(), Platform.code)
        .all()
    )
    return [
        {
            "event_id": event_id,
            "event_date": event_date,
            "event_number": event_number,
            "platform_code": platform_code,
            "finishers_count": int(finishers_count or 0),
        }
        for event_id, event_date, event_number, platform_code, finishers_count in rows
    ]
