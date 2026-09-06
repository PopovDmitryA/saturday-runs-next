"""Постоянный состав: волонтёрский зачёт в срезе по ролям.

Фильтр ролей на странице локации — тот же, что в рейтингах волонтёрского
туризма (просьба Дмитрия 06.09.2026): «показать только тех, кто выходил в
ролях на площадке». Считать его на фронте суммой по ролям нельзя — человек,
взявший в одну субботу две роли, дал бы два волонтёрства вместо одного,
поэтому срез живёт на бэкенде и проверяется здесь.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, VolunteerResult
from app.services.location_page_service import (
    _active_participant_rows,
    normalize_participant_role_filter,
)
from app.volunteer_role_taxonomy import CANONICAL_ROLE_LABELS, preset_role_keys

DAYS = [date(2026, 3, 7), date(2026, 3, 14), date(2026, 3, 21), date(2026, 3, 28)]


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


@pytest.fixture
def location(db_session: Session) -> Location:
    row = Location(
        platform_id=_platform(db_session, "five_verst").id,
        external_key=f"rolefilter-{uuid4().hex[:8]}",
        name="Ролевая площадка",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def participant(db_session: Session) -> Participant:
    row = Participant(
        platform_id=_platform(db_session, "five_verst").id,
        external_user_id=f"98{uuid4().int % 10**9:09d}",
        display_name="Марина Волонтёрова",
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


def _volunteering(db_session: Session, event: Event, participant: Participant, role: str) -> None:
    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}:{role}",
            role=role,
        )
    )
    db_session.flush()


def test_role_filter_counts_distinct_events(
    db_session: Session, location: Location, participant: Participant
) -> None:
    """В зачёт идут только выбранные роли, а суббота с двумя ролями — одна."""
    events = [_event(db_session, location, day) for day in DAYS]
    # Три субботы маршалом, из них в одну — ещё и фотографом (та же суббота).
    for event in events[:3]:
        _volunteering(db_session, event, participant, "Маршал")
    _volunteering(db_session, events[2], participant, "Фотограф")
    # Четвёртая суббота — только фотографом.
    _volunteering(db_session, events[3], participant, "Фотограф")

    event_ids = [event.id for event in events]

    all_roles, _total = _active_participant_rows(
        db_session, VolunteerResult, event_ids, test_event_ids=[]
    )
    row = next(item for item in all_roles if item["name"] == "Марина Волонтёрова")
    # Без фильтра — четыре субботы, а не пять волонтёрств.
    assert row["count"] == 4

    marshal_only, _marshal_total = _active_participant_rows(
        db_session, VolunteerResult, event_ids, test_event_ids=[], role_labels=["Маршал"]
    )
    row = next(item for item in marshal_only if item["name"] == "Марина Волонтёрова")
    assert row["count"] == 3
    # «Всего» тоже считается по выбранным ролям — иначе доля «здесь» врала бы.
    assert row["total_count"] == 3


def test_role_filter_applies_threshold_after_filtering(
    db_session: Session, location: Location, participant: Participant
) -> None:
    """Порог в три участия проверяется уже по отфильтрованным ролям."""
    events = [_event(db_session, location, day) for day in DAYS]
    for event in events:
        _volunteering(db_session, event, participant, "Фотограф")
    for event in events[:2]:
        _volunteering(db_session, event, participant, "Маршал")

    event_ids = [event.id for event in events]
    rows, _total = _active_participant_rows(
        db_session, VolunteerResult, event_ids, test_event_ids=[], role_labels=["Маршал"]
    )
    # Двух маршальств для постоянного состава мало, хотя всего выходов четыре.
    assert [item for item in rows if item["name"] == "Марина Волонтёрова"] == []


def test_role_filter_rejects_runners() -> None:
    """Роли есть только у волонтёрств: фильтр к пробежкам не приезжает молча."""
    from app.models import RunResult

    with pytest.raises(ValueError):
        _active_participant_rows(
            None,  # type: ignore[arg-type]
            RunResult,
            [uuid4()],
            test_event_ids=[],
            role_labels=["Маршал"],
        )


def test_normalize_role_filter_matches_ratings_rules() -> None:
    """Ключ кэша: пресет узнаётся по набору, «все роли» фильтром не считаются."""
    assert normalize_participant_role_filter(None) == (None, "")
    assert normalize_participant_role_filter([]) == (None, "")
    # Полный набор — это «все роли», фильтровать нечего.
    assert normalize_participant_role_filter(list(CANONICAL_ROLE_LABELS)) == (None, "")
    # Незнакомые ключи отбрасываются, а не роняют запрос.
    assert normalize_participant_role_filter(["нет-такой-роли"]) == (None, "")

    on_site = preset_role_keys("on_site")
    assert on_site is not None
    selected, key = normalize_participant_role_filter(sorted(on_site))
    assert selected == on_site
    assert key == "on_site"

    # Свой набор — стабильный хэш: тот же набор в другом порядке даёт тот же ключ.
    pair = ["marshal", "timekeeper"]
    _first, key_a = normalize_participant_role_filter(pair)
    _second, key_b = normalize_participant_role_filter(list(reversed(pair)))
    assert key_a == key_b
    assert key_a.startswith("c")
