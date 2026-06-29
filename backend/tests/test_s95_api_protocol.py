from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, ProtocolSyncState, RunResult, VolunteerResult
from app.s95.api_client import S95ApiActivityRef
from app.s95.parsers.api_protocol import parse_s95_activity
from app.sync.s95_protocol_api import upsert_activity_protocol_api

# Real shape of /activities/1855.json (Пенза, 26.10.2024), trimmed to a few rows.
ACTIVITY_JSON = {
    "date": "26.10.2024",
    "event": {"name": "Пенза", "code_name": "penza", "town": "Пенза (Город Спутник) "},
    "results": [
        {"total_time": "24:27", "position": 1, "athlete": {"id": 15512, "name": "Наталия МАШТАКОВА", "gender": "female", "club": "Доброспутники: ЗаБег"}},
        {"total_time": "24:35", "position": 3, "athlete": {"id": 15723, "name": "Данила СПИРЯКОВ", "gender": "male", "parkrun_code": 1267623}},
        {"total_time": "00:30:38", "position": 5, "athlete": {"id": 14476, "name": "Мария БАГРИЙ", "gender": "female"}},
    ],
    "volunteers": [
        {"role": "director", "athlete": {"id": 14442, "name": "Юлия НИКИФОРОВА", "gender": "female"}},
        {"role": "timer", "athlete": {"id": 15476, "name": "Владислав НИКИФОРОВ", "gender": "male"}},
        {"role": "tokens", "athlete": {"id": 15507, "name": "Мария КАЗАКОВА", "gender": "female"}},
        {"role": "scanner", "athlete": {"id": 14476, "name": "Мария БАГРИЙ", "gender": "female"}},
        {"role": "equipment_supplier", "athlete": {"id": 14444, "name": "Алексей ФИЛИН", "gender": "male"}},
    ],
}


def test_parse_activity_basic():
    parsed = parse_s95_activity(
        ACTIVITY_JSON,
        location_external_key="penza",
        location_name="Пенза",
        source_url="https://s95.ru/activities/1855.json",
    )
    assert parsed.event_date == date(2024, 10, 26)
    assert len(parsed.run_results) == 3
    assert len(parsed.volunteer_results) == 5

    first = parsed.run_results[0]
    assert first.external_user_id == "15512"
    assert first.position == 1
    assert first.finish_time_sec == 24 * 60 + 27  # 24:27 mm:ss
    assert first.external_result_key == "penza:2024-10-26:15512:1"

    # hh:mm:ss form
    third = parsed.run_results[2]
    assert third.finish_time_sec == 30 * 60 + 38

    roles = {v.role for v in parsed.volunteer_results}
    assert "Директор" in roles
    assert "Секундомер" in roles
    assert "Раздача карточек позиций" in roles
    assert "Сканер" in roles
    assert "Доставка оборудования" in roles

    # cross-platform codes captured
    assert parsed.athlete_codes["15723"]["parkrun_code"] == 1267623


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def _make_location(db_session: Session, platform: Platform) -> Location:
    loc = Location(
        platform_id=platform.id,
        external_key=f"penza-{uuid4().hex[:6]}",
        name="Пенза",
        source_url="https://s95.ru/events/penza",
    )
    db_session.add(loc)
    db_session.flush()
    return loc


def test_upsert_protocol_creates_then_unchanged(db_session: Session, s95_platform: Platform):
    location = _make_location(db_session, s95_platform)
    ref = S95ApiActivityRef(date="2024-10-26", url="https://s95.ru/activities/1855.json")

    activity = dict(ACTIVITY_JSON)
    parsed_loc_key = location.external_key

    # First pass: created
    res1 = upsert_activity_protocol_api(
        db_session, s95_platform, location, ref, activity_json=activity
    )
    db_session.flush()
    assert res1.created is True
    assert res1.changed is True
    assert res1.run_results_count == 3

    event = db_session.query(Event).filter(
        Event.platform_id == s95_platform.id,
        Event.external_event_key == f"{parsed_loc_key}:2024-10-26",
    ).one()
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).count() == 3
    assert db_session.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).count() == 5
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    assert state.last_protocol_fetched_at is not None
    first_check = state.last_protocol_check_at

    # codes stored on participant
    p = db_session.query(Participant).filter(
        Participant.platform_id == s95_platform.id, Participant.external_user_id == "15723"
    ).one()
    assert p.profile_extra.get("platform_codes", {}).get("parkrun_code") == 1267623

    # Second pass: unchanged → only bump check time, no rewrite
    res2 = upsert_activity_protocol_api(
        db_session, s95_platform, location, ref, activity_json=activity
    )
    db_session.flush()
    assert res2.changed is False
    db_session.refresh(state)
    assert state.last_protocol_check_at >= first_check
