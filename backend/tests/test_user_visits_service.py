from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import PageViewEvent, User
from app.services.page_analytics_service import record_page_view
from app.services.user_visits_service import VisitPage, get_user_visits, group_visits


def _page(minutes_ago: int, path: str = "/runs", duration: int | None = None) -> VisitPage:
    return VisitPage(
        ts=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago),
        path=path,
        page_type="runs",
        entity_key="",
        duration_sec=duration,
    )


def test_group_visits_splits_by_half_hour_gap() -> None:
    # События приходят от свежих к старым, как их отдаёт запрос.
    events = [_page(0), _page(5), _page(12, duration=60), _page(120), _page(125)]

    visits = group_visits(events)

    assert len(visits) == 2
    recent, earlier = visits
    assert recent.views == 3
    # Заход начинается с самой старой страницы пачки и кончается самой свежей.
    assert recent.started_at == _page(12).ts
    assert recent.duration_sec == 12 * 60
    assert [page.ts for page in recent.pages] == [_page(12).ts, _page(5).ts, _page(0).ts]
    assert earlier.views == 2


def test_group_visits_counts_tail_duration() -> None:
    visits = group_visits([_page(0, duration=90)])

    assert len(visits) == 1
    # Единственную страницу листали полторы минуты — заход не нулевой.
    assert visits[0].duration_sec == 90


def _make_user(db_session: Session) -> User:
    user = User(display_name="Тест визитов")
    db_session.add(user)
    db_session.commit()
    return user


def test_record_page_view_marks_last_seen(db_session: Session) -> None:
    user = _make_user(db_session)
    assert user.last_seen_at is None

    record_page_view(
        db_session,
        view_id=uuid4(),
        path="/runs",
        visitor_key=f"u:{user.id}",
        viewer_user_id=user.id,
    )

    db_session.refresh(user)
    assert user.last_seen_at is not None


def test_get_user_visits_summarizes_window(db_session: Session) -> None:
    user = _make_user(db_session)
    base = datetime.now(timezone.utc) - timedelta(days=3)
    for offset_minutes, path in ((0, "/dashboard"), (10, "/runs"), (600, "/locations")):
        db_session.add(
            PageViewEvent(
                view_id=uuid4(),
                ts=base + timedelta(minutes=offset_minutes),
                path=path,
                page_type=path.strip("/"),
                entity_key="",
                visitor_key=f"u:{user.id}",
                viewer_user_id=user.id,
            )
        )
    # Событие бота в журнал не попадает.
    db_session.add(
        PageViewEvent(
            view_id=uuid4(),
            ts=base,
            path="/runs",
            page_type="runs",
            entity_key="",
            visitor_key=f"u:{user.id}",
            viewer_user_id=user.id,
            is_bot=True,
        )
    )
    db_session.commit()

    journal = get_user_visits(db_session, user.id)

    assert journal.total_views == 3
    # Между заходами больше получаса — это два разных визита.
    assert journal.visits_shown == 2
    assert journal.truncated is False
    assert journal.last_view_at is not None
    assert {page.path for visit in journal.visits for page in visit.pages} == {
        "/dashboard",
        "/runs",
        "/locations",
    }
