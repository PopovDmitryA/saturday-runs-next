"""Личная плашка главной (Т4): что видит залогиненный на /portal/me."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.portal_me_service import build_portal_me


def _platform(db: Session, code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db.add(platform)
        db.commit()
        db.refresh(platform)
    return platform


def _user(db: Session) -> User:
    user = User(display_name="Тест")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _linked_participant(db: Session, user: User, code: str) -> Participant:
    platform = _platform(db, code)
    external_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id, external_user_id=external_id, display_name="Бегун"
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)
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


def _run(
    db: Session,
    participant: Participant,
    day: date,
    seconds: int | None,
    *,
    location_name: str = "Кузьминки",
    is_pr: bool = False,
    is_global_pr: bool = False,
) -> None:
    location = Location(
        platform_id=participant.platform_id,
        external_key=f"loc-{uuid4().hex[:10]}",
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
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"res-{uuid4().hex[:10]}",
            finish_time_sec=seconds,
            is_pr=is_pr,
            is_global_pr=is_global_pr,
        )
    )
    db.commit()


def test_without_links_asks_to_link(db_session: Session) -> None:
    """Аккаунт есть, привязок нет — плашка зовёт привязать профиль."""
    payload = build_portal_me(db_session, _user(db_session))

    assert payload["linked"] is False
    assert payload["last_run"] is None
    assert payload["saturday_streak"] == 0


def test_linked_without_finishes(db_session: Session) -> None:
    """Свежая привязка, синк ещё не принёс результаты — не ошибка, а пустая плашка."""
    user = _user(db_session)
    _linked_participant(db_session, user, "five_verst")

    payload = build_portal_me(db_session, user)

    assert payload["linked"] is True
    assert payload["last_run"] is None


def test_last_run_is_the_freshest(db_session: Session) -> None:
    user = _user(db_session)
    participant = _linked_participant(db_session, user, "five_verst")
    _run(db_session, participant, date(2026, 8, 1), 1_600, location_name="Старая")
    _run(db_session, participant, date(2026, 8, 15), 1_471, location_name="Кузьминки", is_global_pr=True)

    payload = build_portal_me(db_session, user, today=date(2026, 8, 20))
    last = payload["last_run"]

    assert last is not None
    assert last["event_date"] == date(2026, 8, 15)
    assert last["location_name"] == "Кузьминки"
    assert last["finish_time_display"] == "24:31"
    assert last["is_global_pr"] is True


def test_run_without_finish_time_is_not_a_finish(db_session: Session) -> None:
    """Отметка об участии без результата — не пробежка, иначе плашка соврёт."""
    user = _user(db_session)
    participant = _linked_participant(db_session, user, "five_verst")
    _run(db_session, participant, date(2026, 8, 1), 1_600, location_name="С результатом")
    _run(db_session, participant, date(2026, 8, 15), None, location_name="Без результата")

    last = build_portal_me(db_session, user, today=date(2026, 8, 20))["last_run"]

    assert last is not None
    assert last["location_name"] == "С результатом"


def test_saturday_streak_counts_back_from_last_saturday(db_session: Session) -> None:
    user = _user(db_session)
    participant = _linked_participant(db_session, user, "five_verst")
    for weeks_ago in range(3):
        _run(db_session, participant, date(2026, 8, 15) - timedelta(days=7 * weeks_ago), 1_500)

    payload = build_portal_me(db_session, user, today=date(2026, 8, 20))

    assert payload["saturday_streak"] == 3


def test_broken_streak_is_zero(db_session: Session) -> None:
    """Пропущенные субботы — серия оборвана; молчим, а не показываем старую."""
    user = _user(db_session)
    participant = _linked_participant(db_session, user, "five_verst")
    _run(db_session, participant, date(2026, 6, 6), 1_500)

    payload = build_portal_me(db_session, user, today=date(2026, 8, 20))

    assert payload["saturday_streak"] == 0


def test_streak_survives_crosslinked_duplicate(db_session: Session) -> None:
    """Один старт, записанный двумя системами, не удваивает серию.

    Ради этого серия и считается по множеству дат: суммы кабинет
    дедуплицирует отдельной логикой, а множество схлопывает дубли само.
    """
    user = _user(db_session)
    five = _linked_participant(db_session, user, "five_verst")
    s95 = _linked_participant(db_session, user, "s95")
    for weeks_ago in range(2):
        day = date(2026, 8, 15) - timedelta(days=7 * weeks_ago)
        _run(db_session, five, day, 1_500)
        _run(db_session, s95, day, 1_500)

    payload = build_portal_me(db_session, user, today=date(2026, 8, 20))

    assert payload["saturday_streak"] == 2
