"""Досвязка dual_load-протоколов RunPark с парой на основной платформе.

Кросслинк ставится только когда синк RunPark трогает событие, а окно у него —
семь дней. Протокол s95 за 22.08.2026 в Великом Новгороде выложили через двое
суток, забрать его удалось ещё позже — пара уже не склеивалась, и старт уходил
в статистику как RunPark, хотя это тот же забег той же площадки.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, EventCrosslink, Location, Platform, RunparkLocationMapping
from app.sync.runpark_global_sync import backfill_dual_load_crosslinks

EVENT_DATE = date(2026, 8, 22)


def _backfill(db_session: Session, runpark_location: Location) -> int:
    """Разбор сужен до площадки теста: в dev-БД лежат чужие несвязанные протоколы."""
    return backfill_dual_load_crosslinks(db_session, location_ids=[runpark_location.id])


def _platform(db_session: Session, code: str, name: str) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        row = Platform(code=code, name=name, base_url=f"https://{code}.example")
        db_session.add(row)
        db_session.flush()
    return row


def _location(db_session: Session, platform: Platform, prefix: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"{prefix}-{uuid4().hex[:8]}",
        name="Тестовая площадка",
    )
    db_session.add(location)
    db_session.flush()
    return location


def _event(db_session: Session, location: Location, event_date: date) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{event_date.isoformat()}",
        event_date=event_date,
    )
    db_session.add(event)
    db_session.flush()
    return event


@pytest.fixture
def dual_load_pair(db_session: Session) -> tuple[Location, Location]:
    """Локация RunPark с решением dual_load и её пара на s95."""
    runpark_location = _location(db_session, _platform(db_session, "runpark", "RunPark"), "runpark-testpark")
    s95_location = _location(db_session, _platform(db_session, "s95", "с95"), "testpark")
    db_session.add(
        RunparkLocationMapping(
            runpark_location_id=str(uuid4()).upper(),
            runpark_name="Тестовая площадка",
            decision="dual_load",
            show_on_map=False,
            runpark_location_row_id=runpark_location.id,
            matched_location_id=s95_location.id,
            source_batch="test",
        )
    )
    db_session.flush()
    return runpark_location, s95_location


def test_late_primary_protocol_gets_crosslinked(
    db_session: Session, dual_load_pair: tuple[Location, Location]
) -> None:
    runpark_location, s95_location = dual_load_pair
    runpark_event = _event(db_session, runpark_location, EVENT_DATE)
    # Протокол основной платформы доехал позже — уже за пределами окна синка.
    s95_event = _event(db_session, s95_location, EVENT_DATE)

    assert _backfill(db_session, runpark_location) == 1

    link = (
        db_session.query(EventCrosslink)
        .filter(EventCrosslink.secondary_event_id == runpark_event.id)
        .one()
    )
    assert link.primary_event_id == s95_event.id


def test_backfill_is_idempotent(
    db_session: Session, dual_load_pair: tuple[Location, Location]
) -> None:
    runpark_location, s95_location = dual_load_pair
    _event(db_session, runpark_location, EVENT_DATE)
    _event(db_session, s95_location, EVENT_DATE)

    assert _backfill(db_session, runpark_location) == 1
    assert _backfill(db_session, runpark_location) == 0


def test_runpark_only_date_stays_orphan(
    db_session: Session, dual_load_pair: tuple[Location, Location]
) -> None:
    """Без пары на основной платформе связывать нечего — старт остаётся своим."""
    runpark_location, _s95_location = dual_load_pair
    runpark_event = _event(db_session, runpark_location, EVENT_DATE)

    assert _backfill(db_session, runpark_location) == 0
    assert (
        db_session.query(EventCrosslink)
        .filter(EventCrosslink.secondary_event_id == runpark_event.id)
        .count()
        == 0
    )
