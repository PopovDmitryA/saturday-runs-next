"""Постоянный состав: границы стажа считаются в разрезе систем.

Швейцария до 5 вёрст работала в RunPark, и в срезе «5 вёрст» колонка «Первый
старт» показывала 13.05.2023 — дату из RunPark, хотя площадка в 5 вёрст
открылась 29.07.2023 (баг от 03.09.2026).
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunResult
from app.services.location_page_service import _active_participant_rows

RUNPARK_ERA = [date(2023, 5, 13), date(2023, 6, 10), date(2023, 7, 15)]
FIVE_VERST_ERA = [date(2023, 9, 23), date(2023, 10, 7), date(2023, 11, 4)]


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _location(db_session: Session, platform: Platform, prefix: str) -> Location:
    row = Location(
        platform_id=platform.id,
        external_key=f"{prefix}-{uuid4().hex[:8]}",
        name="Швейцария",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _event(db_session: Session, location: Location, event_date: date) -> Event:
    row = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{event_date.isoformat()}",
        event_date=event_date,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def participant(db_session: Session) -> Participant:
    external_user_id = f"99{uuid4().int % 10**9:09d}"
    row = Participant(
        platform_id=_platform(db_session, "five_verst").id,
        external_user_id=external_user_id,
        display_name="Денис Репин",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _run(db_session: Session, event: Event, participant: Participant) -> None:
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
            finish_time_sec=1500,
        )
    )
    db_session.flush()


def test_first_start_is_reported_per_platform(
    db_session: Session, participant: Participant
) -> None:
    """Общая дата остаётся общей, а в разрезе систем — своя у каждой."""
    runpark = _location(db_session, _platform(db_session, "runpark"), "runpark-shveytsariya")
    five_verst = _location(db_session, _platform(db_session, "five_verst"), "shveytsariya")

    events = [_event(db_session, runpark, day) for day in RUNPARK_ERA]
    events += [_event(db_session, five_verst, day) for day in FIVE_VERST_ERA]
    for event in events:
        _run(db_session, event, participant)

    rows, _total = _active_participant_rows(
        db_session,
        RunResult,
        [event.id for event in events],
        test_event_ids=[],
    )
    row = next(item for item in rows if item["name"] == "Денис Репин")

    # Срез «Все» — как был: самая ранняя дата на площадке вообще.
    assert row["first_date"] == RUNPARK_ERA[0].isoformat()
    assert row["last_date"] == FIVE_VERST_ERA[-1].isoformat()

    # Срез по системе — только её даты.
    assert row["platform_first_dates"]["five_verst"] == FIVE_VERST_ERA[0].isoformat()
    assert row["platform_last_dates"]["five_verst"] == FIVE_VERST_ERA[-1].isoformat()
    assert row["platform_first_dates"]["runpark"] == RUNPARK_ERA[0].isoformat()
    assert row["platform_last_dates"]["runpark"] == RUNPARK_ERA[-1].isoformat()
    assert row["platform_counts"] == {"runpark": 3, "five_verst": 3}
