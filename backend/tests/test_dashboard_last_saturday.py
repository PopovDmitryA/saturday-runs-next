"""«Твоя последняя суббота» — день целиком, а не только пробежка.

Раньше герой брался из одних пробежек: тот, кто в субботу только волонтёрил,
видел свою последнюю ПРОБЕЖКУ — хоть месячной давности, — а само
волонтёрство карточка не упоминала (Дмитрий, 08.09.2026).
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.dashboard_service import get_dashboard_payload


def _platform(db: Session, code: str) -> Platform:
    return db.query(Platform).filter(Platform.code == code).one()


def _user(db: Session) -> User:
    user = User(display_name="Тест")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _linked_participant(db: Session, user: User) -> Participant:
    platform = _platform(db, "five_verst")
    external_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id, external_user_id=external_id, display_name="Бегун"
    )
    db.add(participant)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_id,
            external_url="https://example.test/profile",
        )
    )
    db.commit()
    return participant


def _event(db: Session, participant: Participant, day: date, location_name: str) -> Event:
    location = Location(
        platform_id=participant.platform_id,
        external_key=f"ls-{uuid4().hex[:10]}",
        name=location_name,
    )
    db.add(location)
    db.flush()
    event = Event(
        platform_id=participant.platform_id,
        location_id=location.id,
        event_date=day,
        external_event_key=f"ev-{uuid4().hex[:10]}",
    )
    db.add(event)
    db.flush()
    return event


def _run(db: Session, participant: Participant, day: date, seconds: int, location_name: str) -> None:
    event = _event(db, participant, day, location_name)
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"res-{uuid4().hex[:10]}",
            finish_time_sec=seconds,
            position=7,
        )
    )
    db.commit()


def _volunteering(db: Session, participant: Participant, day: date, location_name: str, role: str) -> None:
    event = _event(db, participant, day, location_name)
    db.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"vol-{uuid4().hex[:10]}",
            role=role,
        )
    )
    db.commit()


def _last_saturday(db: Session, user: User) -> dict:
    payload = get_dashboard_payload(db, user)
    stats = payload["stats"]
    analytics = stats["analytics"] if isinstance(stats, dict) else stats.analytics
    last = analytics["last_saturday"] if isinstance(analytics, dict) else analytics.last_saturday
    assert last is not None
    return dict(last) if not isinstance(last, dict) else last


def test_run_day_lists_same_day_volunteering(db_session: Session) -> None:
    """Пробежал в одном месте и помог в другом — карточка знает про оба."""
    user = _user(db_session)
    participant = _linked_participant(db_session, user)
    _run(db_session, participant, date(2026, 8, 29), 1_428, "Лихославль")
    _volunteering(db_session, participant, date(2026, 8, 29), "Мещерский", "Составление отчёта")

    last = _last_saturday(db_session, user)

    assert last["kind"] == "run"
    assert last["location_name"] == "Лихославль"
    assert last["finish_time_sec"] == 1_428
    assert [(v["location_name"], v["role"]) for v in last["volunteering"]] == [
        ("Мещерский", "Составление отчёта")
    ]


def test_volunteer_only_day_becomes_the_hero(db_session: Session) -> None:
    """Свежее волонтёрство важнее старой пробежки: герой — роль, беговых полей нет."""
    user = _user(db_session)
    participant = _linked_participant(db_session, user)
    _run(db_session, participant, date(2026, 8, 1), 1_500, "Кузьминки")
    _volunteering(db_session, participant, date(2026, 8, 29), "Мещерский", "Маршал")

    last = _last_saturday(db_session, user)

    assert last["kind"] == "volunteer"
    assert last["event_date"] == "2026-08-29"
    assert last["location_name"] == "Мещерский"
    assert last["finish_time_sec"] is None
    assert last["position"] is None
    assert last["notables"] == []
    assert [v["role"] for v in last["volunteering"]] == ["Маршал"]


def test_newer_run_wins_over_older_volunteering(db_session: Session) -> None:
    """Волонтёрство прошлой субботы в карточку этой не попадает."""
    user = _user(db_session)
    participant = _linked_participant(db_session, user)
    _volunteering(db_session, participant, date(2026, 8, 22), "Мещерский", "Маршал")
    _run(db_session, participant, date(2026, 8, 29), 1_428, "Лихославль")

    last = _last_saturday(db_session, user)

    assert last["kind"] == "run"
    assert last["event_date"] == "2026-08-29"
    assert last["volunteering"] == []


def test_two_roles_in_one_day_are_listed(db_session: Session) -> None:
    user = _user(db_session)
    participant = _linked_participant(db_session, user)
    _volunteering(db_session, participant, date(2026, 8, 29), "Мещерский", "Маршал")
    _volunteering(db_session, participant, date(2026, 8, 29), "Мещерский", "Фотограф")

    last = _last_saturday(db_session, user)

    assert last["kind"] == "volunteer"
    assert sorted(v["role"] for v in last["volunteering"]) == ["Маршал", "Фотограф"]
