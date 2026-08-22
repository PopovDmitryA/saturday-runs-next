from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Platform, SyncRun, SyncRunStatus
from app.services.sync_run_maintenance import close_stale_sync_runs, stale_after


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    # Платформа уже засеяна миграцией 001 — берём существующую, иначе UniqueViolation.
    row = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        row = Platform(code="five_verst", name="5 verst", base_url="https://5verst.ru")
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
    return row


def test_stale_after_defaults() -> None:
    assert stale_after("five_verst:latest") == timedelta(hours=2)
    assert stale_after("five_verst:location:slug") == timedelta(hours=2)
    assert stale_after("unknown:type") == timedelta(hours=4)


def test_close_stale_sync_runs(db_session, five_verst_platform) -> None:
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    stale = SyncRun(
        platform_id=five_verst_platform.id,
        sync_type="five_verst:latest",
        status=SyncRunStatus.running,
        started_at=now - timedelta(hours=5),
    )
    fresh = SyncRun(
        platform_id=five_verst_platform.id,
        sync_type="five_verst:latest",
        status=SyncRunStatus.running,
        started_at=now - timedelta(minutes=30),
    )
    db_session.add_all([stale, fresh])
    db_session.commit()

    closed = close_stale_sync_runs(db_session, now=now)
    db_session.refresh(stale)
    db_session.refresh(fresh)

    assert closed == 1
    assert stale.status == SyncRunStatus.failed
    assert stale.error_message
    assert fresh.status == SyncRunStatus.running
