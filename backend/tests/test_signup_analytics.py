"""Переходы на регистрацию в беговых системах (канал "signup" в ab_events).

Клик пишет фронт (trackSignupClick), сводку строит build_signup_click_stats
для «Популярности»: система × место клика, и сколько вошедших потом
привязали эту систему.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.admin import user_response
from app.models import Participant, Platform, PlatformLink, User
from app.services.ab_service import record_ab_event
from app.services.page_analytics_service import build_signup_click_stats

# Свой день: сводка считается по всей таблице за период, а общая dev-БД
# помнит события соседних прогонов.
DAY = date(2001, 3, 3)


def _user(db_session: Session) -> User:
    user = User(display_name="Signup Tester", consent_accepted=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _link(
    db_session: Session,
    user: User,
    platform_code: str,
    *,
    linked_at: datetime,
    link_method: str = "url",
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Signup Tester",
        profile_url=f"https://example.test/{external_user_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_user_id,
            external_url=participant.profile_url,
            linked_at=linked_at,
            link_method=link_method,
        )
    )
    db_session.commit()


def _click(db_session: Session, value: str, *, visitor: str, viewer: User | None = None) -> None:
    event = record_ab_event(
        db_session,
        experiment="signup",
        variant="-",
        visitor_key=visitor,
        event_type="signup_click",
        value=value,
        path="/registration",
        viewer=viewer,
    )
    assert event is not None


def _move_to_day(db_session: Session, visitors: list[str]) -> None:
    db_session.execute(
        text(
            "UPDATE ab_events SET ts = :moment "
            "WHERE experiment = 'signup' AND visitor_key = ANY(:keys)"
        ),
        {"moment": datetime.combine(DAY, time(12, 0), tzinfo=timezone.utc), "keys": visitors},
    )
    db_session.commit()


def test_signup_channel_accepts_only_click() -> None:
    """Чужой тип события в канал не пишется — как у остальных каналов."""
    from app.services.ab_service import _EVENT_TYPES_BY_EXPERIMENT

    assert _EVENT_TYPES_BY_EXPERIMENT["signup"] == frozenset({"signup_click"})


def test_unknown_signup_event_is_dropped(db_session: Session) -> None:
    assert (
        record_ab_event(
            db_session,
            experiment="signup",
            variant="-",
            visitor_key="a:signup-junk",
            event_type="cta_click",
            value="s95:map",
        )
        is None
    )


def test_signup_stats_count_linked_after_click(db_session: Session) -> None:
    """«Привязали» — только те, у кого система привязана ПОСЛЕ клика: привязка
    до клика означает, что профиль был и без нас. «Зарегистрировались через
    сайт» — из них привязки со способом signup. Клик гостя, который вошёл
    позже, приписывается ему по visitor_key — это основной путь новичка."""
    after = _user(db_session)
    before = _user(db_session)
    guest = _user(db_session)
    tag = uuid4().hex[:8]
    visitors = [f"a:su-{tag}-1", f"a:su-{tag}-2", f"a:su-{tag}-3"]

    _click(db_session, "s95:location", visitor=visitors[0], viewer=after)
    _click(db_session, "s95:location", visitor=visitors[0], viewer=after)
    _click(db_session, "s95:location", visitor=visitors[1], viewer=before)
    _click(db_session, "s95:location", visitor=visitors[2])
    _click(db_session, "five_verst:page:map", visitor=visitors[2])
    _move_to_day(db_session, visitors)
    # Гость вошёл уже после клика — событие входа знает пользователя.
    assert (
        record_ab_event(
            db_session,
            experiment="funnel",
            variant="-",
            visitor_key=visitors[2],
            event_type="auth_done",
            viewer=guest,
        )
        is not None
    )

    click_moment = datetime.combine(DAY, time(12, 0), tzinfo=timezone.utc)
    _link(db_session, after, "s95", linked_at=click_moment + timedelta(days=2), link_method="signup")
    _link(db_session, before, "s95", linked_at=click_moment - timedelta(days=30))
    _link(db_session, guest, "s95", linked_at=click_moment + timedelta(days=1))

    rows = {
        (row["platform_code"], row["place"]): row
        for row in build_signup_click_stats(db_session, start=DAY, end=DAY)
    }

    s95 = rows[("s95", "location")]
    assert s95["clicks"] == 4
    assert s95["visitors"] == 3
    assert s95["users"] == 3
    assert s95["linked_users"] == 2
    assert s95["signup_users"] == 1

    # Место с «:» внутри (страница регистрации, пришли с карты) не режется.
    page = rows[("five_verst", "page:map")]
    assert page["clicks"] == 1
    assert page["users"] == 1
    assert page["linked_users"] == 0
    assert page["signup_users"] == 0


def test_me_lists_linked_platform_codes(db_session: Session) -> None:
    """/auth/me отдаёт коды привязанных систем — по ним страницы зовут
    зарегистрироваться только туда, где профиля нет."""
    user = _user(db_session)
    _link(db_session, user, "s95", linked_at=datetime.now(timezone.utc))
    _link(db_session, user, "five_verst", linked_at=datetime.now(timezone.utc))

    response = user_response(user, get_settings(), db=db_session)

    assert response.linked_platform_codes == ["five_verst", "s95"]
