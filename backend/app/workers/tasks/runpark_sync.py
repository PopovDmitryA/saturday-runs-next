from __future__ import annotations

import logging
from datetime import date, timedelta
from uuid import UUID

from app.db.session import get_session_factory
from app.sync.runpark_global_sync import sync_runpark_batch
from app.workers.celery_app import celery_app
from app.workers.tasks.sync_task_reporting import run_reported_sync

logger = logging.getLogger(__name__)


@celery_app.task(name="runpark_sync.sync_latest", queue="runpark")
def runpark_sync_latest() -> dict[str, object]:
    since = date.today() - timedelta(days=7)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_runpark_batch(db, since)
            return {
                "since": since.isoformat(),
                "events_total": result.events_total,
                "events_upserted": result.events_upserted,
                "run_results_upserted": result.run_results_upserted,
                "volunteer_results_upserted": result.volunteer_results_upserted,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(
        "runpark latest",
        _run,
        details=f"since {since.isoformat()}",
    )


@celery_app.task(name="runpark_sync.user_sync", queue="runpark")
def runpark_user_sync_task(
    user_id: str,
    trigger: str,
    job_id: str | None = None,
    *,
    platform_link_id: str | None = None,
) -> dict[str, object]:
    from app.models import PlatformLinkSyncStatus
    from app.services.dashboard_service import finish_sync_job
    from app.services.sync_dedup_service import mark_platform_link_sync_status

    since = date.today() - timedelta(days=90)
    db = get_session_factory()()
    try:
        result = sync_runpark_batch(db, since)
        if job_id and platform_link_id:
            mark_platform_link_sync_status(db, UUID(platform_link_id), PlatformLinkSyncStatus.ok)
            finish_sync_job(db, UUID(job_id), success=True)
            db.commit()
        return {
            "since": since.isoformat(),
            "events_total": result.events_total,
            "events_upserted": result.events_upserted,
            "run_results_upserted": result.run_results_upserted,
            "volunteer_results_upserted": result.volunteer_results_upserted,
            "errors": result.errors,
        }
    except Exception:
        logger.exception("RunPark user_sync failed for user %s", user_id)
        if job_id and platform_link_id:
            try:
                mark_platform_link_sync_status(db, UUID(platform_link_id), PlatformLinkSyncStatus.error)
                finish_sync_job(db, UUID(job_id), success=False)
                db.commit()
            except Exception:
                pass
        raise
    finally:
        db.close()
