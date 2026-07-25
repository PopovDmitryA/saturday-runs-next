from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AbEvent, User
from app.services.ab_service import classify_login_cohort, record_ab_event


def _make_user(db_session: Session) -> User:
    user = User(display_name="Тест")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_record_ab_event_persists_fields(db_session: Session) -> None:
    event = record_ab_event(
        db_session,
        experiment="home_v1",
        variant="B",
        visitor_key="a:abc123",
        event_type="scroll_depth",
        value="75",
        path="/",
    )
    assert event is not None
    stored = db_session.query(AbEvent).filter(AbEvent.id == event.id).one()
    assert stored.experiment == "home_v1"
    assert stored.variant == "B"
    assert stored.visitor_key == "a:abc123"
    assert stored.event_type == "scroll_depth"
    assert stored.value == "75"
    assert stored.cohort == ""
    assert stored.viewer_user_id is None


def test_record_ab_event_rejects_unknown_experiment_and_type(db_session: Session) -> None:
    assert (
        record_ab_event(
            db_session,
            experiment="unknown_exp",
            variant="A",
            visitor_key="a:x",
            event_type="scroll_depth",
        )
        is None
    )
    assert (
        record_ab_event(
            db_session,
            experiment="home_v1",
            variant="A",
            visitor_key="a:x",
            event_type="made_up_event",
        )
        is None
    )
    assert db_session.query(AbEvent).count() == 0


def test_login_complete_requires_viewer(db_session: Session) -> None:
    assert (
        record_ab_event(
            db_session,
            experiment="home_v1",
            variant="A",
            visitor_key="a:x",
            event_type="login_complete",
        )
        is None
    )


def test_login_complete_cohorts(db_session: Session) -> None:
    user = _make_user(db_session)

    # Свежесозданный аккаунт — регистрация.
    fresh = record_ab_event(
        db_session,
        experiment="home_v1",
        variant="B",
        visitor_key="a:abc",
        event_type="login_complete",
        viewer=user,
    )
    assert fresh is not None
    assert fresh.cohort == "new"
    assert fresh.viewer_user_id == user.id

    # Старый аккаунт (создан давно) — вернувшийся участник, не конверсия.
    assert classify_login_cohort(user, now=datetime.now(timezone.utc) + timedelta(days=30)) == "returning"


def test_classify_login_cohort_naive_created_at(db_session: Session) -> None:
    user = _make_user(db_session)
    user.created_at = datetime.now() - timedelta(minutes=5)  # noqa: DTZ005 — намеренно naive
    assert classify_login_cohort(user) == "new"
