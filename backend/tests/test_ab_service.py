from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import AbEvent, User
from app.services.ab_service import record_ab_event


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
        variant="-",
        visitor_key="a:abc123",
        event_type="home_link_click",
        value="location:pushkinsky",
        path="/",
    )
    assert event is not None
    stored = db_session.query(AbEvent).filter(AbEvent.id == event.id).one()
    assert stored.experiment == "home_v1"
    assert stored.variant == "-"
    assert stored.visitor_key == "a:abc123"
    assert stored.event_type == "home_link_click"
    assert stored.value == "location:pushkinsky"
    assert stored.cohort == ""
    assert stored.viewer_user_id is None


def test_record_ab_event_keeps_viewer(db_session: Session) -> None:
    user = _make_user(db_session)
    event = record_ab_event(
        db_session,
        experiment="home_v1",
        variant="-",
        visitor_key="a:abc",
        event_type="home_link_click",
        value="runner:42",
        viewer=user,
    )
    assert event is not None
    assert event.viewer_user_id == user.id


def test_record_ab_event_rejects_unknown_experiment_and_type(db_session: Session) -> None:
    before = db_session.query(AbEvent).count()
    assert (
        record_ab_event(
            db_session,
            experiment="unknown_exp",
            variant="-",
            visitor_key="a:x",
            event_type="home_link_click",
        )
        is None
    )
    assert (
        record_ab_event(
            db_session,
            experiment="home_v1",
            variant="-",
            visitor_key="a:x",
            event_type="made_up_event",
        )
        is None
    )
    assert db_session.query(AbEvent).count() == before


@pytest.mark.parametrize(
    "event_type",
    ["variant_view", "scroll_depth", "cta_view", "cta_click", "period", "chart_tab", "login_complete"],
)
def test_finished_experiment_events_are_rejected(db_session: Session, event_type: str) -> None:
    """АБ-тест главной завершён: его события больше не принимаем.

    Вкладки со старым бандлом могут слать их ещё долго — такие запросы должны
    молча отбрасываться, а не дописываться в закрытую выборку теста.
    """
    before = db_session.query(AbEvent).count()
    assert (
        record_ab_event(
            db_session,
            experiment="home_v1",
            variant="B",
            visitor_key="a:stale-tab",
            event_type=event_type,
            viewer=_make_user(db_session),
        )
        is None
    )
    assert db_session.query(AbEvent).count() == before


def test_home_link_click_is_a_known_event(db_session: Session) -> None:
    """Переход по ссылке с главной пишется — тип в белом списке ab_service."""
    event = record_ab_event(
        db_session,
        experiment="home_v1",
        variant="-",
        visitor_key="a:link-1",
        event_type="home_link_click",
        value="location:pushkinsky",
        path="/",
    )
    assert event is not None
    assert event.value == "location:pushkinsky"
