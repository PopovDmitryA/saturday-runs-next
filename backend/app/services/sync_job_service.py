from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from celery.result import AsyncResult
from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, SyncJob, SyncJobStatus
from app.services.celery_queue_inspector import (
    TASK_QUEUE_BY_SUFFIX,
    celery_task_id_for_job,
    find_task_queue_position,
    get_celery_task_state,
    task_is_worker_reserved,
)
from app.services.sync_error_format import humanize_sync_error_message
from app.workers.celery_app import celery_app

STALE_JOB_SECONDS = 120
LOST_TASK_SECONDS = 180


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _task_suffixes_for_job(db: Session, job: SyncJob) -> list[str]:
    if job.platform_link_id is not None:
        row = (
            db.query(Platform.code)
            .join(PlatformLink, PlatformLink.platform_id == Platform.id)
            .filter(PlatformLink.id == job.platform_link_id)
            .one_or_none()
        )
        if row is None:
            return []
        code = row[0]
        if code == "five_verst":
            return ["five_verst"]
        if code == "s95":
            return ["s95"]
        if code == "parkrun":
            return ["parkrun"]
        return []

    rows = (
        db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == job.user_id)
        .all()
    )
    codes = {code for (code,) in rows}
    suffixes: list[str] = []
    if "five_verst" in codes:
        suffixes.append("five_verst")
    if "s95" in codes:
        suffixes.append("s95")
    if "parkrun" in codes:
        suffixes.append("parkrun")
    return suffixes


def fail_sync_job(job_id: UUID, error_message: str) -> None:
    from app.db.session import get_session_factory

    db = get_session_factory()()
    try:
        job = db.query(SyncJob).filter(SyncJob.id == job_id).one_or_none()
        if job is None:
            return
        if job.status not in {SyncJobStatus.queued, SyncJobStatus.running}:
            return
        job.status = SyncJobStatus.failed
        job.error_message = (humanize_sync_error_message(error_message) or error_message)[:2000]
        job.finished_at = _utcnow()
        if job.started_at is None:
            job.started_at = job.finished_at
        db.commit()
    finally:
        db.close()


def _celery_failure_message(task_id: str) -> str | None:
    result = AsyncResult(task_id, app=celery_app)
    if result.state != "FAILURE":
        return None
    try:
        exc = result.result
        raw = str(exc) if exc else "Ошибка воркера"
        return humanize_sync_error_message(raw) or raw
    except Exception:
        return "Ошибка воркера"


def _reconcile_job(db: Session, job: SyncJob) -> bool:
    if job.status not in {SyncJobStatus.queued, SyncJobStatus.running}:
        return False

    age_seconds = (_utcnow() - job.created_at).total_seconds()
    suffixes = _task_suffixes_for_job(db, job)
    if not suffixes:
        if age_seconds >= STALE_JOB_SECONDS:
            job.status = SyncJobStatus.failed
            job.error_message = "Нет привязанных профилей для этой задачи"
            job.finished_at = _utcnow()
            return True
        return False

    states: list[str] = []
    failure_messages: list[str] = []
    pending_lost = True
    waiting_in_queue = False

    for suffix in suffixes:
        task_id = celery_task_id_for_job(job.id, suffix)
        state = get_celery_task_state(task_id)
        states.append(state)

        if state == "FAILURE":
            message = _celery_failure_message(task_id)
            if message:
                failure_messages.append(f"{suffix}: {message}")

        if state == "PENDING":
            queue_name = TASK_QUEUE_BY_SUFFIX[suffix]
            if find_task_queue_position(queue_name, task_id) is not None:
                pending_lost = False
                waiting_in_queue = True
            elif task_is_worker_reserved(task_id):
                pending_lost = False
        else:
            pending_lost = False

    if failure_messages:
        job.status = SyncJobStatus.failed
        job.error_message = (
            humanize_sync_error_message("; ".join(failure_messages)) or "; ".join(failure_messages)
        )[:2000]
        job.finished_at = _utcnow()
        if job.started_at is None:
            job.started_at = job.finished_at
        return True

    if any(state == "STARTED" for state in states):
        if job.status != SyncJobStatus.running:
            job.status = SyncJobStatus.running
            job.started_at = job.started_at or _utcnow()
            return True

    if all(state == "SUCCESS" for state in states):
        job.status = SyncJobStatus.success
        job.finished_at = _utcnow()
        job.error_message = None
        if job.started_at is None:
            job.started_at = job.finished_at
        return True

    if pending_lost and age_seconds >= LOST_TASK_SECONDS and all(state == "PENDING" for state in states):
        job.status = SyncJobStatus.failed
        job.error_message = (
            "Задача не была обработана воркером (сбой или перезапуск). "
            "Нажмите «Обновить» на нужной платформе ещё раз."
        )
        job.finished_at = _utcnow()
        if job.started_at is None:
            job.started_at = job.finished_at
        return True

    if (
        age_seconds >= STALE_JOB_SECONDS * 15
        and all(state == "PENDING" for state in states)
        and not waiting_in_queue
    ):
        job.status = SyncJobStatus.failed
        job.error_message = "Превышено время ожидания в очереди"
        job.finished_at = _utcnow()
        if job.started_at is None:
            job.started_at = job.finished_at
        return True

    return False


def reconcile_user_sync_jobs(db: Session, user_id: UUID) -> int:
    jobs = (
        db.query(SyncJob)
        .filter(
            SyncJob.user_id == user_id,
            SyncJob.status.in_([SyncJobStatus.queued, SyncJobStatus.running]),
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    changed = 0
    for job in jobs:
        if _reconcile_job(db, job):
            changed += 1
    if changed:
        db.commit()
    return changed
