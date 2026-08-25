"""Batch-синк RunPark: неизменные протоколы пропускаются по хэшу содержимого."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Platform,
    ProtocolSyncState,
    RunparkLocationMapping,
    RunResult,
)
from app.sync.runpark_global_sync import sync_runpark_batch

# Фейковые GUID'ы, каких нет в реальных данных dev-БД.
EVENT_ID = "AAAA1111-2222-3333-4444-555566667777"
LOCATION_ID = "BBBB1111-2222-3333-4444-555566667777"
RESULT_ID = "CCCC1111-2222-3333-4444-555566667777"
PARTICIPANT_ID = "DDDD1111-2222-3333-4444-555566667777"


@pytest.fixture
def runpark_location(db_session: Session) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if platform is None:
        platform = Platform(code="runpark", name="RunPark", base_url="https://runpark.ru")
        db_session.add(platform)
        db_session.flush()
    location = Location(
        platform_id=platform.id,
        external_key=f"runpark-testpark-{uuid4().hex[:8]}",
        name="Тестовый парк",
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        RunparkLocationMapping(
            runpark_location_id=LOCATION_ID,
            runpark_name="Тестовый парк",
            decision="load_history",
            show_on_map=True,
            runpark_location_row_id=location.id,
            source_batch="test",
        )
    )
    db_session.flush()
    return location


def _fake_rows(finish_time_sec: int) -> dict[str, list[dict]]:
    event_date = datetime(2026, 8, 1, 9, 0)
    return {
        "vw_events": [
            {
                "event_id": EVENT_ID,
                "location_id": LOCATION_ID,
                "event_date": event_date,
                "event_number": 12,
                "is_test_event": False,
                "finishers_count": 1,
            }
        ],
        "vw_run_results": [
            {
                "result_id": RESULT_ID,
                "event_id": EVENT_ID,
                "event_date": event_date,
                "participant_id": PARTICIPANT_ID,
                "participant_name": "Тестовый Бегун",
                "barcode_id": "A12345",
                "position": 1,
                "finish_time_sec": finish_time_sec,
                "finish_time_display": "00:20:00",
                "age_category": "М30-34",
                "status": "finished",
                "is_pr": False,
            }
        ],
        "vw_volunteer_results": [],
    }


def _patched_query(rows: dict[str, list[dict]]):
    def fake_runpark_query(sql: str, params: tuple = ()) -> list[dict]:
        for view, payload in rows.items():
            if view in sql:
                return payload
        raise AssertionError(f"Unexpected runpark query: {sql}")

    return patch("app.sync.runpark_global_sync.runpark_query", side_effect=fake_runpark_query)


def _test_event(db_session: Session) -> Event:
    return db_session.query(Event).filter(Event.external_event_key == EVENT_ID).one()


def test_unchanged_event_skips_reload(db_session: Session, runpark_location: Location) -> None:
    with _patched_query(_fake_rows(finish_time_sec=1200)):
        first = sync_runpark_batch(db_session, date(2026, 7, 25))
    assert first.errors == []
    assert first.events_upserted == 1
    assert first.events_unchanged == 0

    event = _test_event(db_session)
    row_before = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    assert state.protocol_source_hash is not None

    # Повторный прогон с теми же данными: без delete+reinsert.
    with _patched_query(_fake_rows(finish_time_sec=1200)):
        second = sync_runpark_batch(db_session, date(2026, 7, 25))
    assert second.errors == []
    assert second.events_upserted == 0
    assert second.events_unchanged == 1

    row_after = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert row_after.id == row_before.id  # строка не пересоздавалась


def test_changed_event_reloads_results(db_session: Session, runpark_location: Location) -> None:
    with _patched_query(_fake_rows(finish_time_sec=1200)):
        sync_runpark_batch(db_session, date(2026, 7, 25))
    event = _test_event(db_session)
    old_hash = (
        db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one().protocol_source_hash
    )

    with _patched_query(_fake_rows(finish_time_sec=1100)):
        result = sync_runpark_batch(db_session, date(2026, 7, 25))
    assert result.errors == []
    assert result.events_upserted == 1
    assert result.events_unchanged == 0
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).one().finish_time_sec == 1100
    new_hash = (
        db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one().protocol_source_hash
    )
    assert new_hash != old_hash
