from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import SyncJob, SyncJobStatus, SyncJobTrigger, User
from app.services.sync_job_service import reconcile_user_sync_jobs


def test_reconcile_marks_stale_pending_as_failed(db_session: Session) -> None:
    user = User(telegram_id=999_000_001 + int(uuid4().int % 1000))
    db_session.add(user)
    db_session.flush()

    job = SyncJob(
        user_id=user.id,
        trigger=SyncJobTrigger.manual,
        status=SyncJobStatus.queued,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(job)
    db_session.commit()

    with (
        patch("app.services.sync_job_service._task_suffixes_for_job", return_value=["five_verst"]),
        patch("app.services.sync_job_service.get_celery_task_state", return_value="PENDING"),
        patch("app.services.sync_job_service.find_task_queue_position", return_value=None),
    ):
        changed = reconcile_user_sync_jobs(db_session, user.id)

    assert changed == 1
    db_session.refresh(job)
    assert job.status == SyncJobStatus.failed
    assert job.error_message is not None


def test_reconcile_keeps_job_queued_when_task_is_in_broker_queue(db_session: Session) -> None:
    user = User(telegram_id=999_000_002 + int(uuid4().int % 1000))
    db_session.add(user)
    db_session.flush()

    job = SyncJob(
        user_id=user.id,
        trigger=SyncJobTrigger.manual,
        status=SyncJobStatus.queued,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(job)
    db_session.commit()

    with (
        patch("app.services.sync_job_service._task_suffixes_for_job", return_value=["five_verst"]),
        patch("app.services.sync_job_service.get_celery_task_state", return_value="PENDING"),
        patch("app.services.sync_job_service.find_task_queue_position", return_value=9),
    ):
        changed = reconcile_user_sync_jobs(db_session, user.id)

    assert changed == 0
    db_session.refresh(job)
    assert job.status == SyncJobStatus.queued
    assert job.error_message is None


def test_reconcile_does_not_timeout_while_waiting_in_queue(db_session: Session) -> None:
    user = User(telegram_id=999_000_003 + int(uuid4().int % 1000))
    db_session.add(user)
    db_session.flush()

    job = SyncJob(
        user_id=user.id,
        trigger=SyncJobTrigger.manual,
        status=SyncJobStatus.queued,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=45),
    )
    db_session.add(job)
    db_session.commit()

    with (
        patch("app.services.sync_job_service._task_suffixes_for_job", return_value=["five_verst"]),
        patch("app.services.sync_job_service.get_celery_task_state", return_value="PENDING"),
        patch("app.services.sync_job_service.find_task_queue_position", return_value=50),
    ):
        changed = reconcile_user_sync_jobs(db_session, user.id)

    assert changed == 0
    db_session.refresh(job)
    assert job.status == SyncJobStatus.queued
