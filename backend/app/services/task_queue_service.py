from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, SyncJob, SyncJobStatus, User
from app.services.admin_pipeline_status_service import get_admin_pipeline_status
from app.services.celery_queue_inspector import (
    PARKRUN_SYNC_QUEUE,
    S95_SYNC_QUEUE,
    TASK_QUEUE_BY_SUFFIX,
    USER_SYNC_QUEUE,
    get_queue_length,
    inspect_user_task,
)
from app.services.sync_error_format import present_sync_error

ACTIVE_JOB_STATUSES = frozenset({SyncJobStatus.queued, SyncJobStatus.running})
RECENT_JOBS_LIMIT = 30
ADMIN_RECENT_JOBS_LIMIT = 100


def _platform_codes_for_user(db: Session, user_id: UUID) -> set[str]:
    rows = (
        db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    return {code for (code,) in rows}


def _task_suffixes_for_job(
    db: Session,
    user_id: UUID,
    job: SyncJob,
    user_platforms: set[str],
) -> list[str]:
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

    suffixes: list[str] = []
    if "five_verst" in user_platforms:
        suffixes.append("five_verst")
    if "s95" in user_platforms:
        suffixes.append("s95")
    if "parkrun" in user_platforms:
        suffixes.append("parkrun")
    return suffixes


def _platform_label_for_job(
    db: Session,
    job: SyncJob,
    suffixes: list[str],
) -> str | None:
    if job.platform_link_id is not None:
        row = (
            db.query(Platform.code)
            .join(PlatformLink, PlatformLink.platform_id == Platform.id)
            .filter(PlatformLink.id == job.platform_link_id)
            .one_or_none()
        )
        return row[0] if row else None
    if len(suffixes) == 1:
        return suffixes[0]
    if len(suffixes) > 1:
        return "all"
    return None


def _queue_summaries() -> list[dict[str, object]]:
    return [
        {"queue": USER_SYNC_QUEUE, "label": "профили 5 вёрст", "length": get_queue_length(USER_SYNC_QUEUE)},
        {"queue": S95_SYNC_QUEUE, "label": "профили с95", "length": get_queue_length(S95_SYNC_QUEUE)},
        {"queue": PARKRUN_SYNC_QUEUE, "label": "профили parkrun", "length": get_queue_length(PARKRUN_SYNC_QUEUE)},
    ]


def _pipeline_payload(db: Session) -> dict[str, object]:
    status = get_admin_pipeline_status(db)
    running: list[dict[str, object]] = []
    for item in status.get("running") or []:
        running.append(
            {
                "label": item.get("label"),
                "started_at": item.get("started_at"),
                "source": item.get("source"),
            }
        )
    last_success: list[dict[str, object]] = []
    for item in status.get("last_success") or []:
        last_success.append(
            {
                "label": item.get("label"),
                "finished_at": item.get("finished_at"),
                "source": "sync_run",
            }
        )
    return {
        "running": running,
        "last_success": last_success,
        "queue_depths": status.get("queue_depths") or {},
        "checked_at": status.get("checked_at"),
    }


def _serialize_job(
    db: Session,
    job: SyncJob,
    user_id: UUID,
    user_platforms: set[str],
    *,
    user: User | None = None,
) -> dict[str, object]:
    suffixes = _task_suffixes_for_job(db, user_id, job, user_platforms)
    platform_code = _platform_label_for_job(db, job, suffixes)

    tasks: list[dict[str, object]] = []
    if job.status in ACTIVE_JOB_STATUSES:
        for suffix in suffixes:
            inspected = inspect_user_task(job.id, suffix)
            if inspected is not None:
                tasks.append(inspected)

    error_message, error_details = present_sync_error(job.error_message)
    item: dict[str, object] = {
        "id": job.id,
        "trigger": job.trigger.value,
        "status": job.status.value,
        "platform_code": platform_code,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_message": error_message,
        "error_details": error_details,
        "tasks": tasks,
    }
    if user is not None:
        item["user"] = {
            "telegram_id": user.telegram_id,
            "telegram_username": user.telegram_username,
            "display_name": user.display_name,
        }
    return item


def get_user_task_queue_payload(db: Session, user_id: UUID) -> dict[str, object]:
    from app.services.sync_job_service import reconcile_user_sync_jobs

    reconcile_user_sync_jobs(db, user_id)

    jobs = (
        db.query(SyncJob)
        .filter(SyncJob.user_id == user_id)
        .order_by(SyncJob.created_at.desc())
        .limit(RECENT_JOBS_LIMIT)
        .all()
    )
    user_platforms = _platform_codes_for_user(db, user_id)

    job_items = [_serialize_job(db, job, user_id, user_platforms) for job in jobs]
    active_count = sum(1 for job in jobs if job.status in ACTIVE_JOB_STATUSES)

    return {
        "jobs": job_items,
        "queues": _queue_summaries(),
        "active_jobs_count": active_count,
        "task_queue_by_suffix": TASK_QUEUE_BY_SUFFIX,
    }


def get_admin_task_queue_payload(db: Session) -> dict[str, object]:
    from app.services.sync_job_service import reconcile_user_sync_jobs

    active_user_ids = (
        db.query(SyncJob.user_id)
        .filter(SyncJob.status.in_(tuple(ACTIVE_JOB_STATUSES)))
        .distinct()
        .all()
    )
    for (user_id,) in active_user_ids:
        reconcile_user_sync_jobs(db, user_id)

    rows = (
        db.query(SyncJob, User)
        .join(User, SyncJob.user_id == User.id)
        .order_by(SyncJob.created_at.desc())
        .limit(ADMIN_RECENT_JOBS_LIMIT)
        .all()
    )

    platforms_cache: dict[UUID, set[str]] = {}
    job_items: list[dict[str, object]] = []
    for job, user in rows:
        user_platforms = platforms_cache.get(user.id)
        if user_platforms is None:
            user_platforms = _platform_codes_for_user(db, user.id)
            platforms_cache[user.id] = user_platforms
        job_items.append(_serialize_job(db, job, user.id, user_platforms, user=user))

    active_count = sum(1 for job, _user in rows if job.status in ACTIVE_JOB_STATUSES)

    return {
        "jobs": job_items,
        "queues": _queue_summaries(),
        "active_jobs_count": active_count,
        "task_queue_by_suffix": TASK_QUEUE_BY_SUFFIX,
        "pipeline": _pipeline_payload(db),
    }
