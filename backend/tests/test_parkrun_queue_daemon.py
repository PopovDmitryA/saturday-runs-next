from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    SyncJob,
    SyncJobStatus,
    SyncJobTrigger,
    User,
)
from app.services.parkrun_queue_daemon import build_parkrun_work_queue


def _user(db_session: Session) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    return user


def _parkrun_platform(db_session: Session) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        platform = Platform(code="parkrun", name="parkrun", base_url="https://www.parkrun.org.uk")
        db_session.add(platform)
        db_session.flush()
    return platform


def _errored_link(
    db_session: Session, platform: Platform, user: User, *, trigger: SyncJobTrigger
) -> PlatformLink:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"runner-{uuid4().hex[:8]}",
        display_name="Runner",
    )
    db_session.add(participant)
    db_session.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id,
        external_user_id=participant.external_user_id,
        external_url=f"https://parkrun.example/{participant.external_user_id}",
        sync_status=PlatformLinkSyncStatus.error,
    )
    db_session.add(link)
    db_session.flush()
    db_session.add(
        SyncJob(
            user_id=user.id,
            platform_link_id=link.id,
            trigger=trigger,
            status=SyncJobStatus.failed,
        )
    )
    db_session.commit()
    return link


def test_manual_refresh_click_jumps_ahead_of_pending_backlog(db_session: Session) -> None:
    platform = _parkrun_platform(db_session)
    manual_user = _user(db_session)
    manual_link = _errored_link(db_session, platform, manual_user, trigger=SyncJobTrigger.manual)

    login_user = _user(db_session)
    login_link = _errored_link(db_session, platform, login_user, trigger=SyncJobTrigger.login)

    items = build_parkrun_work_queue(db_session, limit_pending=50)

    kinds_and_users = [(item.kind, item.user_id) for item in items]
    assert kinds_and_users[0] == ("sync", manual_link.user_id)
    assert ("sync", login_link.user_id) == kinds_and_users[-1]


def test_only_the_latest_sync_job_decides_priority(db_session: Session) -> None:
    """Первый клик был login-триггером, повторный ручной клик поверх той же
    ошибки должен подтолкнуть линк вперёд — приоритет по ПОСЛЕДНЕЙ попытке."""
    platform = _parkrun_platform(db_session)
    user = _user(db_session)
    link = _errored_link(db_session, platform, user, trigger=SyncJobTrigger.login)
    db_session.add(
        SyncJob(
            user_id=user.id,
            platform_link_id=link.id,
            trigger=SyncJobTrigger.manual,
            status=SyncJobStatus.failed,
            # Postgres' now() is frozen for the whole transaction, so within a
            # single test transaction both jobs would otherwise tie on
            # created_at; a real manual click always lands in a later,
            # separate request/transaction than the original auto-sync job.
            created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    )
    db_session.commit()

    other_user = _user(db_session)
    _errored_link(db_session, platform, other_user, trigger=SyncJobTrigger.login)

    items = build_parkrun_work_queue(db_session, limit_pending=50)
    assert items[0].user_id == user.id
