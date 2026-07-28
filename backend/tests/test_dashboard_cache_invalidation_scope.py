"""Сужение инвалидации dashboard_cache до реально затронутых пользователей.

Раньше любой упсерт результатов 5 вёрст сносил кэш всем привязанным
пользователям, и полный пересчёт (десятки секунд) доставался первому, кто
откроет профиль. Теперь синк сообщает момент старта, а затронуты те, чьи
СОБСТВЕННЫЕ результаты он трогал, плюс держатели рекордов локаций — у
последних дашборд может устареть без их участия, если рекорд перебили.

Сужать по локациям пробовали и отказались: наши бегуны — «туристы», любая
затронутая площадка задевает 80–90% пользователей. По своим результатам за
час синка набирается ~9%.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    DashboardCache,
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.dashboard_service import (
    invalidate_dashboard_cache_for_users,
    locations_touched_since,
    users_holding_location_records,
    users_with_touched_results,
)


def _platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db.add(platform)
        db.flush()
    return platform


def _user_with_participant(db: Session, platform: Platform, suffix: str) -> tuple[User, Participant]:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"scope-{suffix}-{uuid4().hex[:6]}",
        display_name=f"Бегун {suffix}",
    )
    db.add(participant)
    db.flush()
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name=f"Бегун {suffix}")
    db.add(user)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.com",
        )
    )
    db.add(DashboardCache(user_id=user.id, computed_at=datetime.now(timezone.utc), stats={}))
    db.flush()
    return user, participant


def _location(db: Session, platform: Platform, suffix: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"scope-{suffix}-{uuid4().hex[:6]}",
        name=f"Парк {suffix}",
        city="Москва",
    )
    db.add(location)
    db.flush()
    return location


def _result(
    db: Session,
    platform: Platform,
    location: Location,
    participant: Participant,
    *,
    fetched_at: datetime,
) -> None:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"scope-event-{uuid4().hex[:10]}",
        event_date=date(2025, 5, 3),
        title=location.name,
    )
    db.add(event)
    db.flush()
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
            finish_time_sec=1200,
            age_category="М30-34",
            status="finished",
            fetched_at=fetched_at,
        )
    )
    db.flush()


def test_touched_locations_and_users_are_limited_to_the_synced_protocol(
    db_session: Session,
) -> None:
    """Синк тронул одну площадку — вторая и её бегуны остаются с живым кэшем."""
    platform = _platform(db_session)
    started_at = datetime.now(timezone.utc)

    touched_location = _location(db_session, platform, "touched")
    quiet_location = _location(db_session, platform, "quiet")

    runner_there, participant_there = _user_with_participant(db_session, platform, "there")
    runner_elsewhere, participant_elsewhere = _user_with_participant(db_session, platform, "elsewhere")

    # Протокол этой площадки только что перезабрали.
    _result(
        db_session,
        platform,
        touched_location,
        participant_there,
        fetched_at=started_at + timedelta(seconds=5),
    )
    # А этот результат лежит с прошлой недели и синком не тронут.
    _result(
        db_session,
        platform,
        quiet_location,
        participant_elsewhere,
        fetched_at=started_at - timedelta(days=7),
    )

    locations = locations_touched_since(db_session, started_at)
    assert touched_location.id in locations
    assert quiet_location.id not in locations

    user_ids = users_with_touched_results(db_session, started_at)
    assert runner_there.id in user_ids
    assert runner_elsewhere.id not in user_ids


def test_invalidation_removes_cache_only_for_listed_users(db_session: Session) -> None:
    """Кэш сносится точечно, а не всем подряд, как было раньше."""
    platform = _platform(db_session)
    affected, _participant_a = _user_with_participant(db_session, platform, "affected")
    untouched, _participant_b = _user_with_participant(db_session, platform, "untouched")

    deleted = invalidate_dashboard_cache_for_users(db_session, {affected.id})

    assert deleted == 1
    remaining = {
        row.user_id
        for row in db_session.query(DashboardCache)
        .filter(DashboardCache.user_id.in_([affected.id, untouched.id]))
        .all()
    }
    assert remaining == {untouched.id}


def test_empty_user_set_is_a_no_op(db_session: Session) -> None:
    """Синк без затронутых локаций не должен сносить вообще ничего."""
    platform = _platform(db_session)
    kept, _participant = _user_with_participant(db_session, platform, "kept")

    assert invalidate_dashboard_cache_for_users(db_session, set()) == 0
    assert (
        db_session.query(DashboardCache).filter(DashboardCache.user_id == kept.id).one_or_none()
        is not None
    )


def test_record_holders_are_invalidated_even_without_own_results(db_session: Session) -> None:
    """Держателя рекорда задевает чужой забег — его кэш тоже надо снести."""
    platform = _platform(db_session)
    holder, _participant_a = _user_with_participant(db_session, platform, "holder")
    plain, _participant_b = _user_with_participant(db_session, platform, "plain")

    holder_cache = (
        db_session.query(DashboardCache).filter(DashboardCache.user_id == holder.id).one()
    )
    holder_cache.stats = {
        "analytics": {
            "location_records": {"current_count": 1, "lost_count": 0, "entries": []},
            "age_group_records": {"current_count": 0, "lost_count": 0, "entries": []},
        }
    }
    plain_cache = db_session.query(DashboardCache).filter(DashboardCache.user_id == plain.id).one()
    plain_cache.stats = {
        "analytics": {
            "location_records": {"current_count": 0, "lost_count": 3, "entries": []},
            "age_group_records": {"current_count": 0, "lost_count": 0, "entries": []},
        }
    }
    db_session.flush()

    holders = users_holding_location_records(db_session)

    assert holder.id in holders
    # Утерянные рекорды уже утеряны — чужой забег их не меняет.
    assert plain.id not in holders
