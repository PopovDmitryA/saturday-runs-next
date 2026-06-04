from __future__ import annotations

from uuid import UUID

from app.db.session import get_session_factory
from app.models import SyncJobTrigger
from app.services.sync_job_service import fail_sync_job
from app.sync.user_sync import run_user_sync
from app.workers.celery_app import celery_app


@celery_app.task(name="user_sync.run")
def user_sync_task(
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
            from app.models import SyncJob

            existing_job = db.query(SyncJob).filter(SyncJob.id == UUID(job_id)).one_or_none()

        job = run_user_sync(
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
