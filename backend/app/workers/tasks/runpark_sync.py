from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from app.db.session import get_session_factory
from app.models import SyncJob, SyncJobTrigger
from app.services.sync_job_service import fail_sync_job
from app.sync.runpark_global_sync import sync_runpark_batch, sync_runpark_for_participant
from app.workers.celery_app import celery_app
from app.workers.tasks.sync_task_reporting import run_reported_sync

logger = logging.getLogger(__name__)


@celery_app.task(name="runpark_sync.sync_latest", queue="runpark")
def runpark_sync_latest() -> dict[str, object]:
    since = date.today() - timedelta(days=7)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            started_at = datetime.now(timezone.utc)
            result = sync_runpark_batch(db, since)
            # Батч RunPark идёт своим расписанием (3,8,13,18,23), мимо окон
            # синка 5 вёрст — без этого его результаты попадали в прогрев
            # дашбордов только по совпадению таймингов.
            if result.run_results_upserted > 0:
                db.commit()
                from app.workers.tasks.dashboard_warm import schedule_dashboard_warm

                schedule_dashboard_warm(started_at)
            return {
                "since": since.isoformat(),
                "events_total": result.events_total,
                "events_upserted": result.events_upserted,
                "events_unchanged": result.events_unchanged,
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
    from app.models import (
        Platform,
        PlatformLink,
        PlatformLinkSyncStatus,
        SyncJobStatus,
        User,
    )
    from app.services.dashboard_service import create_sync_job, recompute_dashboard_cache

    db = get_session_factory()()
    try:
        user = db.query(User).filter(User.id == UUID(user_id)).one_or_none()
        if user is None:
            raise ValueError(f"User not found: {user_id}")

        existing_job = None
        if job_id:
            existing_job = db.query(SyncJob).filter(SyncJob.id == UUID(job_id)).one_or_none()

        job = existing_job or create_sync_job(
            db, UUID(user_id), SyncJobTrigger(trigger),
            platform_link_id=UUID(platform_link_id) if platform_link_id else None,
        )
        job.status = SyncJobStatus.running
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        link_query = (
            db.query(PlatformLink, Platform)
            .join(Platform, PlatformLink.platform_id == Platform.id)
            .filter(PlatformLink.user_id == UUID(user_id), Platform.code == "runpark")
        )
        if platform_link_id:
            link_query = link_query.filter(PlatformLink.id == UUID(platform_link_id))

        links = link_query.all()
        if not links:
            job.status = SyncJobStatus.success
            job.finished_at = datetime.now(timezone.utc)
            recompute_dashboard_cache(db, UUID(user_id))
            db.commit()
            return {"job_id": str(job.id), "status": job.status.value}

        errors: list[str] = []
        for link, platform in links:
            if not platform.is_active:
                continue
            link.sync_status = PlatformLinkSyncStatus.syncing
            db.commit()
            try:
                # external_user_id is the RunPark participant_id UUID
                result = sync_runpark_for_participant(db, link.external_user_id)
                link.sync_status = PlatformLinkSyncStatus.ok
                link.error_message = None
                link.last_user_sync_at = datetime.now(timezone.utc)
                if result.errors:
                    errors.extend(result.errors)
                db.commit()
            except Exception as exc:
                db.rollback()
                link = db.query(PlatformLink).filter(PlatformLink.id == link.id).one()
                job = db.query(SyncJob).filter(SyncJob.id == job.id).one()
                errors.append(str(exc))
                link.sync_status = PlatformLinkSyncStatus.error
                link.error_message = str(exc)[:2000]
                db.commit()

        recompute_dashboard_cache(db, UUID(user_id))
        job.status = SyncJobStatus.success if not errors else SyncJobStatus.failed
        job.finished_at = datetime.now(timezone.utc)
        job.error_message = "; ".join(errors) if errors else None
        db.commit()
        return {"job_id": str(job.id), "status": job.status.value, "errors": errors}

    except Exception as exc:
        logger.exception("RunPark user_sync failed for user %s", user_id)
        if job_id:
            fail_sync_job(UUID(job_id), str(exc))
        raise
    finally:
        db.close()
