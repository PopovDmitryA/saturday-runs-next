from __future__ import annotations

from uuid import UUID

from app.db.session import get_session_factory
from app.models import SyncJob, SyncJobTrigger
from app.services.sync_job_service import fail_sync_job
from app.sync.parkrun_user_sync import run_parkrun_user_sync
from app.workers.celery_app import celery_app


@celery_app.task(name="parkrun_sync.run_user_sync", queue="parkrun")
def parkrun_user_sync_task(
    user_id: str,
    trigger: str,
    job_id: str | None = None,
    *,
    platform_link_id: str | None = None,
) -> dict[str, object]:
    db = get_session_factory()()
    try:
        existing_job = None
        if job_id:
            existing_job = db.query(SyncJob).filter(SyncJob.id == UUID(job_id)).one_or_none()

        job = run_parkrun_user_sync(
            db,
            UUID(user_id),
            SyncJobTrigger(trigger),
            platform_link_id=UUID(platform_link_id) if platform_link_id else None,
            existing_job=existing_job,
        )
        return {
            "job_id": str(job.id),
            "status": job.status.value,
            "error_message": job.error_message,
        }
    except Exception as exc:
        if job_id:
            fail_sync_job(UUID(job_id), str(exc))
        raise
    finally:
        db.close()


@celery_app.task(name="parkrun_sync.process_pending_queue", queue="parkrun")
def parkrun_process_pending_queue_task() -> dict[str, object]:
    """Серверный разбор очереди profile_fetch_pending (parkrun).

    Обрабатываются только строки с user_id — то, что пользователи явно запросили
    через ЛК; discovery-строки без пользователя остаются Mac-демону. Задача
    пропускается, пока жив Mac-демон или активно эскалирующее охлаждение; первый
    фетч после истечения ступени служит пробой разбана — капча снова поднимает
    охлаждение, и разбор останавливается до следующей ступени.
    """
    from app.config import get_settings
    from app.models import ProfileFetchPending, ProfileFetchPendingStatus
    from app.platform_fetch.cooldown import is_platform_in_cooldown
    from app.services.parkrun_local_worker import is_worker_alive
    from app.services.profile_fetch_pending_service import process_pending_row

    settings = get_settings()
    if not settings.parkrun_server_queue_enabled:
        return {"skipped": "disabled"}
    if is_worker_alive():
        return {"skipped": "mac_daemon_alive"}
    if is_platform_in_cooldown("parkrun"):
        return {"skipped": "cooldown"}

    db = get_session_factory()()
    try:
        rows = (
            db.query(ProfileFetchPending)
            .filter(
                ProfileFetchPending.platform_code == "parkrun",
                ProfileFetchPending.status == ProfileFetchPendingStatus.pending,
                ProfileFetchPending.user_id.isnot(None),
            )
            .order_by(ProfileFetchPending.created_at.asc())
            .limit(settings.parkrun_server_queue_batch_size)
            .all()
        )
        outcomes: dict[str, str] = {}
        for row in rows:
            outcome = process_pending_row(db, row)
            outcomes[row.external_user_id or row.profile_input] = outcome
            if outcome in ("cooldown", "skipped_cooldown"):
                break
        return {"processed": len(outcomes), "outcomes": outcomes}
    finally:
        db.close()
