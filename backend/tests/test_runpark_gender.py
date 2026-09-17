"""Пол у RunPark: своя колонка источника старше догадки по возрастной категории.

Репорт 13.09.2026: «при отсутствии возрастной группы у бегунов не определяется
пол М/Ж, хотя в протоколе он проставлен». Пол мы выводили из второй буквы
категории, а её у трети финишей нет вовсе. 17.09.2026 RunPark добавил во вьюху
забегов колонку `gender` — эти тесты стерегут, что мы её читаем и что она
главнее категории.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunparkLocationMapping, RunResult
from app.services.gender_position_service import (
    GENDER_FEMALE,
    GENDER_MALE,
    normalize_source_gender,
)
from app.sync.runpark_global_sync import sync_runpark_batch

EVENT_ID = "EEEE1111-2222-3333-4444-555566667777"
LOCATION_ID = "EEEE2222-2222-3333-4444-555566667777"


@pytest.fixture
def runpark_location(db_session: Session) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if platform is None:
        platform = Platform(code="runpark", name="RunPark", base_url="https://runpark.ru")
        db_session.add(platform)
        db_session.flush()
    location = Location(
        platform_id=platform.id,
        external_key=f"runpark-gender-{uuid4().hex[:8]}",
        name="Парк пола",
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        RunparkLocationMapping(
            runpark_location_id=LOCATION_ID,
            runpark_name="Парк пола",
            decision="load_history",
            show_on_map=True,
            runpark_location_row_id=location.id,
            source_batch="test",
        )
    )
    db_session.flush()
    return location


def _rows(finishers: list[tuple[int, str, str | None, str | None]]) -> dict[str, list[dict]]:
    """Протокол из строк «(место, имя, возрастная категория, пол источника)»."""
    event_date = datetime(2026, 9, 12, 9, 0)
    return {
        "vw_events": [
            {
                "event_id": EVENT_ID,
                "location_id": LOCATION_ID,
                "event_date": event_date,
                "event_number": 5,
                "is_test_event": False,
                "finishers_count": len(finishers),
            }
        ],
        "vw_run_results": [
            {
                "result_id": f"{index:08d}-2222-3333-4444-555566667777",
                "event_id": EVENT_ID,
                "event_date": event_date,
                "participant_id": f"{index:08d}-9999-3333-4444-555566667777",
                "participant_name": name,
                "barcode_id": None,
                "position": position,
                "finish_time_sec": 1200 + position,
                "finish_time_display": "00:20:00",
                "age_category": age_category,
                "gender": gender,
                "status": "finished",
                "is_pr": False,
            }
            for index, (position, name, age_category, gender) in enumerate(finishers, 1)
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


def _protocol(db_session: Session) -> list[tuple[int, str | None, int | None]]:
    event = db_session.query(Event).filter(Event.external_event_key == EVENT_ID).one()
    rows = (
        db_session.query(RunResult.position, Participant.gender, RunResult.gender_position)
        .join(Participant, Participant.id == RunResult.participant_id)
        .filter(RunResult.event_id == event.id)
        .order_by(RunResult.position)
        .all()
    )
    return [(row.position, row.gender, row.gender_position) for row in rows]


def test_gender_letters_are_understood() -> None:
    assert normalize_source_gender("M") == GENDER_MALE
    assert normalize_source_gender("W") == GENDER_FEMALE
    assert normalize_source_gender(" w ") == GENDER_FEMALE
    assert normalize_source_gender("female") == GENDER_FEMALE


def test_unknown_gender_spelling_stays_unknown() -> None:
    """Лучше «пол неизвестен», чем выдуманный: в зачёт по полу такие не идут."""
    assert normalize_source_gender(None) is None
    assert normalize_source_gender("") is None
    assert normalize_source_gender("не указан") is None
    assert normalize_source_gender("X") is None


def test_runner_without_age_category_gets_gender_from_source(
    db_session: Session, runpark_location: Location
) -> None:
    """Тот самый баг: категории нет, а пол источник называет — и место считается."""
    rows = _rows(
        [
            (1, "Константин Кучеренко", "VM50-54", "M"),
            (2, "Дмитрий ГЛУХОВ", None, "M"),
            (3, "Мария Мартынова", "JW15-17", "W"),
            (4, "Кирилл ИЛЛЮШЕНКО", "", "M"),
        ]
    )
    with _patched_query(rows):
        result = sync_runpark_batch(db_session, date(2026, 9, 5))
    assert result.errors == []

    assert _protocol(db_session) == [
        (1, GENDER_MALE, 1),
        (2, GENDER_MALE, 2),
        (3, GENDER_FEMALE, 1),
        (4, GENDER_MALE, 3),
    ]


def test_source_gender_wins_over_age_category(
    db_session: Session, runpark_location: Location
) -> None:
    """Расхождений в данных нет, но если появятся — верим системе, не букве."""
    with _patched_query(_rows([(1, "Спорная Строка", "VM40-44", "W")])):
        sync_runpark_batch(db_session, date(2026, 9, 5))

    assert _protocol(db_session) == [(1, GENDER_FEMALE, 1)]


def test_silent_source_keeps_the_age_category_fallback(
    db_session: Session, runpark_location: Location
) -> None:
    """Колонки может не быть (старый снимок вьюхи) — тогда работает как раньше."""
    rows = _rows([(1, "Без Пола", "VW35-39", None)])
    for row in rows["vw_run_results"]:
        row.pop("gender")

    with _patched_query(rows):
        sync_runpark_batch(db_session, date(2026, 9, 5))

    assert _protocol(db_session) == [(1, GENDER_FEMALE, 1)]
