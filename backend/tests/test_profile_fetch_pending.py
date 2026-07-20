from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Platform,
    ProfileFetchPending,
    ProfileFetchPendingOperation,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
    User,
)
from app.parkrun.errors import ParkrunBanDetected
from app.services.profile_fetch_pending_service import (
    RUNPARK_SEED_NOTE_PREFIX,
    enqueue_profile_fetch_pending,
    is_fetch_cooldown_error,
    list_pending_rows,
    requeue_stuck_done_parkrun_pending,
)
from app.services.profile_linking_service import ProfileLinkingError, preview_profile_link


def _sample_user(db_session: Session) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    return user


def test_is_fetch_cooldown_error() -> None:
    from app.parkrun.errors import ParkrunProfileParseError

    assert is_fetch_cooldown_error(ParkrunBanDetected("parkrun fetch in cooldown until 9999999999"))
    assert is_fetch_cooldown_error(Exception("cooldown until 123"))
    assert is_fetch_cooldown_error(
        ParkrunProfileParseError("Ban/protection page detected")
    )
    wrapped = ParkrunProfileParseError("wrapped")
    wrapped.__cause__ = ParkrunBanDetected("ban")
    assert is_fetch_cooldown_error(wrapped)


def test_enqueue_dedupes_by_external_id(db_session: Session) -> None:
    user = _sample_user(db_session)
    exc = ParkrunBanDetected("parkrun fetch in cooldown until 1780444000")
    first = enqueue_profile_fetch_pending(
        db_session,
        platform_code="parkrun",
        profile_input="3197430",
        user_id=user.id,
        exc=exc,
    )
    second = enqueue_profile_fetch_pending(
        db_session,
        platform_code="parkrun",
        profile_input="https://www.parkrun.org.uk/parkrunner/3197430/",
        user_id=user.id,
        exc=exc,
    )
    db_session.commit()
    assert first.id == second.id
    assert first.external_user_id == "3197430"
    pending = list_pending_rows(db_session, platform_code="parkrun")
    assert len(pending) == 1
    assert pending[0].status == ProfileFetchPendingStatus.pending


def test_preview_enqueues_on_cooldown(db_session: Session, monkeypatch) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    platform.is_active = True
    user = _sample_user(db_session)

    def _raise_cooldown(_parsed):
        raise ParkrunBanDetected("parkrun fetch in cooldown until 1780445000")

    # preview_profile_link(user=...) for parkrun goes through
    # persist_live_profile_preview -> fetch_profile_preview_activity, not
    # adapter.fetch_profile_preview — mocking the adapter method here leaves
    # this call hitting a real (browserless) Playwright launch instead.
    monkeypatch.setattr(
        "app.parkrun.parsers.athlete.fetch_profile_preview_activity", _raise_cooldown
    )

    try:
        preview_profile_link(db_session, "parkrun", "3197430", user=user)
        raise AssertionError("expected ProfileLinkingError")
    except ProfileLinkingError as exc:
        assert exc.status_code == 503
        assert "добавлена в очередь" in exc.message

    pending = list_pending_rows(db_session, platform_code="parkrun")
    assert len(pending) == 1
    assert pending[0].external_user_id == "3197430"


def test_requeue_stuck_done_parkrun_without_platform_link(db_session: Session) -> None:
    user = _sample_user(db_session)
    row = ProfileFetchPending(
        user_id=user.id,
        platform_code="parkrun",
        profile_input="3197430",
        external_user_id="3197430",
        status=ProfileFetchPendingStatus.done,
    )
    db_session.add(row)
    db_session.commit()

    assert requeue_stuck_done_parkrun_pending(db_session) == 1
    db_session.refresh(row)
    assert row.status == ProfileFetchPendingStatus.pending
    assert row.attempts == 0


def test_list_pending_rows_prioritizes_user_requests_over_system_backlog(
    db_session: Session,
) -> None:
    """Строка без user_id (системный discovery/seed-backlog) не должна вставать
    впереди реального пользовательского запроса, даже если она старше.

    platform_code — приватный для теста, чтобы не смешиваться с реальным
    parkrun-backlog'ом, который уже лежит в БД."""
    platform_code = "test-priority-check"
    user = _sample_user(db_session)
    system_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="old-system-row",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.cooldown,
        user_id=None,
    )
    db_session.add(system_row)
    db_session.flush()
    user_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="new-user-row",
        operation=ProfileFetchPendingOperation.profile_preview,
        reason=ProfileFetchPendingReason.cooldown,
        user_id=user.id,
    )
    db_session.add(user_row)
    db_session.commit()

    pending = list_pending_rows(db_session, platform_code=platform_code, limit=10)
    assert [row.profile_input for row in pending] == ["new-user-row", "old-system-row"]


def test_list_pending_rows_prioritizes_runpark_seed_over_discovery_backlog(
    db_session: Session,
) -> None:
    """RunPark-архив гарантирует историю у атлета; discovery (s95-декаплинг
    и т.п.) — лишь гипотеза, что профиль вообще существует. Первое должно
    сортироваться раньше второго внутри безымянного системного backlog'а."""
    platform_code = "test-priority-check-runpark"
    discovery_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="discovery-row",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.error,
        last_error="queued by s95 sync (parkrun decoupled from s95)",
        user_id=None,
    )
    db_session.add(discovery_row)
    db_session.flush()
    runpark_row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input="runpark-row",
        operation=ProfileFetchPendingOperation.activity_import,
        reason=ProfileFetchPendingReason.error,
        last_error=RUNPARK_SEED_NOTE_PREFIX,
        user_id=None,
    )
    db_session.add(runpark_row)
    db_session.commit()

    pending = list_pending_rows(db_session, platform_code=platform_code, limit=10)
    assert [row.profile_input for row in pending] == ["runpark-row", "discovery-row"]
