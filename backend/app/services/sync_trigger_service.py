from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, SyncJobTrigger
from app.services.celery_queue_inspector import celery_task_id_for_job


def _apply_task_id(job_id: UUID | None, suffix: str) -> dict[str, str]:
    if job_id is None:
        return {}
    return {"task_id": celery_task_id_for_job(job_id, suffix)}


def enqueue_user_sync(
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    job_id: UUID | None = None,
    platform_link_id: UUID | None = None,
) -> None:
    from app.workers.tasks.user_sync import user_sync_task

    user_sync_task.apply_async(
        args=[str(user_id), trigger.value, str(job_id) if job_id else None],
        kwargs={"platform_link_id": str(platform_link_id) if platform_link_id else None},
        **_apply_task_id(job_id, "five_verst"),
    )


def enqueue_parkrun_user_sync(
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    job_id: UUID | None = None,
    platform_link_id: UUID | None = None,
) -> None:
    from app.workers.tasks.parkrun_sync import parkrun_user_sync_task

    parkrun_user_sync_task.apply_async(
        args=[str(user_id), trigger.value, str(job_id) if job_id else None],
        kwargs={"platform_link_id": str(platform_link_id) if platform_link_id else None},
        queue="parkrun",
        **_apply_task_id(job_id, "parkrun"),
    )


def enqueue_s95_user_sync(
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    job_id: UUID | None = None,
    platform_link_id: UUID | None = None,
) -> None:
    from app.workers.tasks.s95_sync import s95_user_sync_task

    s95_user_sync_task.apply_async(
        args=[str(user_id), trigger.value, str(job_id) if job_id else None],
        kwargs={"platform_link_id": str(platform_link_id) if platform_link_id else None},
        queue="s95",
        **_apply_task_id(job_id, "s95"),
    )


def _user_has_platform_link(db: Session, user_id: UUID, platform_code: str) -> bool:
    return (
        db.query(PlatformLink)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id, Platform.code == platform_code)
        .count()
        > 0
    )


def maybe_enqueue_login_auto_sync(db: Session, user_id: UUID, *, interval_seconds: int) -> bool:
    from app.services.user_auto_sync_service import maybe_enqueue_login_auto_sync

    return maybe_enqueue_login_auto_sync(db, user_id, interval_seconds=interval_seconds)
