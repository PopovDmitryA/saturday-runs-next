"""Постоянный счётчик воронки регистрации: приём событий и сводка."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AbEvent, User
from app.services.ab_service import record_ab_event
from app.services.page_analytics_service import build_funnel_stats


def _visitor(db_session: Session, key: str, *, viewer: User | None = None, steps=()) -> None:
    for event_type, value in steps:
        record_ab_event(
            db_session,
            experiment="funnel",
            variant="-",
            visitor_key=key,
            event_type=event_type,
            value=value,
            path="/",
            viewer=viewer if event_type == "auth_done" else None,
        )


def _move_to_day(db_session: Session, day: date, keys: list[str]) -> None:
    """Переносит события ЭТИХ посетителей в свой день теста.

    Сводка считается по всей таблице за период, а тесты в одном прогоне пишут в
    общую БД и не откатываются. Свой день изолирует тест от соседей; фильтр по
    ключам обязателен — иначе в него уехали бы и события тестов, которые день
    себе не назначали.
    """
    db_session.execute(
        text(
            "UPDATE ab_events SET ts = :moment "
            "WHERE experiment = 'funnel' AND visitor_key = ANY(:keys)"
        ),
        {"moment": datetime.combine(day, time(12, 0), tzinfo=timezone.utc), "keys": keys},
    )
    db_session.commit()


def _make_user(db_session: Session, *, age_days: float = 0.0) -> User:
    from datetime import datetime, timezone

    user = User(display_name="Тест")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    if age_days:
        user.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        db_session.commit()
        db_session.refresh(user)
    return user


def test_auth_done_without_viewer_is_dropped(db_session: Session) -> None:
    """Вход без пользователя бессмысленен: когорту не из чего вывести."""
    before = db_session.query(AbEvent).count()
    assert (
        record_ab_event(
            db_session,
            experiment="funnel",
            variant="-",
            visitor_key="a:x",
            event_type="auth_done",
        )
        is None
    )
    assert db_session.query(AbEvent).count() == before


def test_auth_done_cohorts(db_session: Session) -> None:
    """Свежий аккаунт — регистрация, старый — вернувшийся участник."""
    fresh = record_ab_event(
        db_session,
        experiment="funnel",
        variant="-",
        visitor_key="a:fresh",
        event_type="auth_done",
        viewer=_make_user(db_session),
    )
    assert fresh is not None and fresh.cohort == "new"

    old = record_ab_event(
        db_session,
        experiment="funnel",
        variant="-",
        visitor_key="a:old",
        event_type="auth_done",
        viewer=_make_user(db_session, age_days=30),
    )
    assert old is not None and old.cohort == "returning"


@pytest.mark.parametrize("event_type", ["home_view", "cta_click", "auth_start"])
def test_funnel_events_are_accepted(db_session: Session, event_type: str) -> None:
    assert (
        record_ab_event(
            db_session,
            experiment="funnel",
            variant="-",
            visitor_key="a:steps",
            event_type=event_type,
            value="hero",
        )
        is not None
    )


def test_build_funnel_counts_people_not_events(db_session: Session) -> None:
    """Пять открытий главной и три клика одного человека — это один человек."""
    key = "a:funnel-one"
    _visitor(
        db_session,
        key,
        steps=[("home_view", ""), ("home_view", ""), ("cta_click", "hero"), ("cta_click", "bottom")],
    )
    day = date(2026, 1, 5)
    _move_to_day(db_session, day, [key])
    rows = build_funnel_stats(db_session, start=day, end=day)
    by_step = {row["step"]: row for row in rows}

    assert by_step["Открыли главную"]["visitors"] == 1
    assert by_step["Нажали кнопку входа"]["visitors"] == 1
    assert by_step["Дошли до провайдера"]["visitors"] == 0


def test_build_funnel_ignores_visitors_without_home_view(db_session: Session) -> None:
    """Вход по прямой ссылке на /login не должен раздувать числитель.

    Иначе конверсия «от открытия главной» считалась бы по людям, которые
    главную вообще не видели, и превышала бы сто процентов.
    """
    _visitor(db_session, "a:home-only", steps=[("home_view", "")])
    _visitor(db_session, "a:direct-login", steps=[("auth_start", "vk")])

    day = date(2026, 1, 6)
    _move_to_day(db_session, day, ["a:home-only", "a:direct-login"])
    rows = build_funnel_stats(db_session, start=day, end=day)
    by_step = {row["step"]: row for row in rows}

    assert by_step["Открыли главную"]["visitors"] == 1
    assert by_step["Дошли до провайдера"]["visitors"] == 0


def test_build_funnel_percentages(db_session: Session) -> None:
    """Проценты: сквозной от первой ступени и локальный от предыдущей."""
    for i in range(4):
        _visitor(db_session, f"a:pct-{i}", steps=[("home_view", "")])
    for i in range(2):
        _visitor(db_session, f"a:pct-{i}", steps=[("cta_click", "hero")])

    day = date(2026, 1, 7)
    _move_to_day(db_session, day, [f"a:pct-{i}" for i in range(4)])
    rows = build_funnel_stats(db_session, start=day, end=day)
    by_step = {row["step"]: row for row in rows}

    assert by_step["Открыли главную"]["pct_of_start"] == 100.0
    assert by_step["Открыли главную"]["pct_of_prev"] is None
    assert by_step["Нажали кнопку входа"]["visitors"] == 2
    assert by_step["Нажали кнопку входа"]["pct_of_start"] == 50.0
    assert by_step["Нажали кнопку входа"]["pct_of_prev"] == 50.0


def test_build_funnel_returning_row_has_no_percentages(db_session: Session) -> None:
    """Вернувшиеся — справка рядом с воронкой, а не её ступень."""
    _visitor(db_session, "a:ret", steps=[("home_view", "")])
    record_ab_event(
        db_session,
        experiment="funnel",
        variant="-",
        visitor_key="a:ret",
        event_type="auth_done",
        viewer=_make_user(db_session, age_days=30),
    )
    day = date(2026, 1, 8)
    _move_to_day(db_session, day, ["a:ret"])
    rows = build_funnel_stats(db_session, start=day, end=day)
    returning = next(row for row in rows if row["step"].startswith("— из них"))

    assert returning["visitors"] == 1
    assert returning["pct_of_start"] is None
    assert returning["pct_of_prev"] is None
