from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    Platform,
    PlatformLink,
    SyncJob,
    SyncJobStatus,
    User,
)
from app.platform_adapters.registry import ensure_adapters_registered
from app.services.celery_queue_inspector import celery_task_id_for_job
from app.services.dashboard_service import recompute_dashboard_cache
from app.services.profile_linking_service import ProfileLinkingError, _get_active_platform
from app.services.profile_preview_cache import clear_profile_preview_cache
from app.services.user_display_name_service import rebind_display_name_source, selected_source_code

ACTIVE_SYNC_JOB_STATUSES = frozenset({SyncJobStatus.queued, SyncJobStatus.running})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _task_suffixes_for_link(db: Session, link: PlatformLink) -> list[str]:
    row = (
        db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.id == link.id)
        .one_or_none()
    )
    if row is None:
        return []
    code = row[0]
    if code in ("five_verst", "s95", "parkrun"):
        return [code]
    return []


def _revoke_celery_tasks_for_job(job_id: UUID, suffixes: list[str]) -> None:
    from app.workers.celery_app import celery_app

    for suffix in suffixes:
        task_id = celery_task_id_for_job(job_id, suffix)
        celery_app.control.revoke(task_id, terminate=True)


def _cancel_active_sync_jobs(db: Session, link: PlatformLink) -> int:
    jobs = (
        db.query(SyncJob)
        .filter(
            SyncJob.platform_link_id == link.id,
            SyncJob.status.in_(ACTIVE_SYNC_JOB_STATUSES),
        )
        .all()
    )
    now = _utcnow()
    for job in jobs:
        suffixes = _task_suffixes_for_link(db, link)
        if suffixes:
            _revoke_celery_tasks_for_job(job.id, suffixes)
        job.status = SyncJobStatus.failed
        job.error_message = "Синхронизация отменена: профиль отвязан"
        job.finished_at = now
        if job.started_at is None:
            job.started_at = now
    return len(jobs)


def _detach_sync_job_references(db: Session, link_id: UUID) -> None:
    db.query(SyncJob).filter(SyncJob.platform_link_id == link_id).update(
        {SyncJob.platform_link_id: None},
        synchronize_session=False,
    )


def unlink_user_profile(db: Session, user: User, platform_code: str) -> dict[str, object]:
    """
    Отвязка профиля платформы от личного кабинета.

    - Удаляется только platform_links (связь user ↔ participant).
    - Participant, events, run_results остаются в глобальном ядре данных.
    - Активные sync_jobs по этой связи отменяются; ссылки в истории jobs обнуляются.
    - Пересчитывается dashboard_cache пользователя.
    """
    ensure_adapters_registered()
    _get_active_platform(db, platform_code)

    link = (
        db.query(PlatformLink)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id, Platform.code == platform_code)
        .one_or_none()
    )
    if link is None:
        raise ProfileLinkingError("Профиль на этой платформе не привязан", 404)

    cancelled_jobs = _cancel_active_sync_jobs(db, link)
    _detach_sync_job_references(db, link.id)
    clear_profile_preview_cache(user.id, platform_code, link.external_url)

    db.delete(link)
    db.flush()
    # Имя могло приходить именно из этой системы. Источник пересматриваем даже
    # при ручном выборе — выбранной системы больше нет, выбирать не из чего.
    if selected_source_code(db, user) == platform_code:
        user.display_name_source_manual = False
    rebind_display_name_source(db, user)
    recompute_dashboard_cache(db, user.id)
    db.commit()

    return {
        "platform_code": platform_code,
        "cancelled_sync_jobs": cancelled_jobs,
        "message": "unlinked",
    }
