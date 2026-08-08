from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from app.db.session import get_session_factory
from app.models import SyncJob, SyncJobTrigger
from app.s95.fetch.priority import s95_user_sync_context
from app.services.sync_job_service import fail_sync_job
from app.services.sync_run_params import s95_registry_details
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry
from app.sync.s95_user_sync import run_s95_user_sync
from app.workers.celery_app import celery_app
from app.workers.s95_batch_yield import run_s95_batch_reported_sync
from app.workers.tasks.sync_task_reporting import run_reported_sync

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.sync.s95_global_sync_api import S95ApiSyncResult

logger = logging.getLogger(__name__)


def _run_api_sync(sync: Callable[[Session], S95ApiSyncResult]) -> dict[str, object]:
    """Прогнать батч S95 и разбудить прогрев дашбордов, если что-то записали.

    До 08.08.2026 прогрев планировался только из синка 5 вёрст, и забеги S95
    попадали в его окно лишь по случайности: у 27 человек на «Обзоре» неделями
    не хватало пробежек, хотя во вкладках они были.
    """
    db = get_session_factory()()
    try:
        started_at = datetime.now(timezone.utc)
        result = sync(db)
        if result.protocols_created or result.protocols_updated:
            db.commit()
            from app.workers.tasks.dashboard_warm import schedule_dashboard_warm

            schedule_dashboard_warm(started_at)
        return asdict(result)
    finally:
        db.close()


@celery_app.task(name="s95_sync.run_user_sync", queue="s95_user")
def s95_user_sync_task(
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

        with s95_user_sync_context():
            job = run_s95_user_sync(
                db,
                UUID(user_id),
                SyncJobTrigger(trigger),
                platform_link_id=UUID(platform_link_id) if platform_link_id else None,
                existing_job=existing_job,
                enqueue_on_ban=True,
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


@celery_app.task(name="s95_sync.sync_locations_registry", queue="s95")
def s95_sync_locations_registry_task(limit: int | None = None, *, force: bool = False) -> dict[str, object]:
    name = "s95 registry /activities"
    details = s95_registry_details(limit=limit)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_s95_locations_registry(
                db,
                S95LocationRegistrySyncOptions(limit=limit),
            )
            return asdict(result)
        finally:
            db.close()

    return run_s95_batch_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="s95:registry",
        force=force,
        reenqueue=lambda: s95_sync_locations_registry_task.apply_async(
            kwargs={"limit": limit, "force": force},
            queue="s95",
        ),
    )


@celery_app.task(name="s95_sync.enqueue_locations_registry", queue="s95")
def s95_enqueue_locations_registry() -> dict[str, object]:
    s95_sync_locations_registry_task.apply_async(kwargs={"force": True}, queue="s95")
    return {"enqueued": 1}


@celery_app.task(name="s95_sync.api_new_protocols", queue="s95")
def s95_api_new_protocols_task() -> dict[str, object]:
    """Import new protocols and refresh changed ones (via JSON API updated_at). Weekend scan —
    catches same-day results and same-day edits."""
    from app.sync.s95_global_sync_api import sync_updated_protocols

    def _run() -> dict[str, object]:
        return _run_api_sync(sync_updated_protocols)

    return run_reported_sync("s95 API: новые протоколы", _run)


@celery_app.task(name="s95_sync.api_sync_updated", queue="s95")
def s95_api_sync_updated_task() -> dict[str, object]:
    """Import new protocols and refresh any whose server-side updated_at moved past what we
    have stored. Runs 3x/week across all locations."""
    from app.sync.s95_global_sync_api import sync_updated_protocols

    def _run() -> dict[str, object]:
        return _run_api_sync(sync_updated_protocols)

    return run_reported_sync("s95 API: обновлённые протоколы", _run)


@celery_app.task(name="s95_sync.api_reconcile_date", queue="s95")
def s95_api_reconcile_date_task(weeks_ago: int = 0) -> dict[str, object]:
    """Re-fetch all protocols dated (most recent Saturday - weeks_ago weeks) to pick up
    late edits. Updates changed protocols and bumps the reviewed timestamp on the rest."""
    from datetime import date, timedelta

    from app.sync.s95_global_sync_api import most_recent_saturday, reconcile_protocols_for_date

    target = most_recent_saturday(date.today()) - timedelta(weeks=weeks_ago)

    def _run() -> dict[str, object]:
        return _run_api_sync(lambda db: reconcile_protocols_for_date(db, target))

    return run_reported_sync(
        "s95 API: сверка протоколов",
        _run,
        details=f"дата {target.isoformat()} (−{weeks_ago} нед)",
    )


@celery_app.task(name="s95_sync.api_full_backfill", queue="s95")
def s95_api_full_backfill_task(limit_per_location: int | None = None) -> dict[str, object]:
    """One-time full pass over every protocol. Run manually, not on a schedule."""
    from app.sync.s95_global_sync_api import full_backfill

    def _run() -> dict[str, object]:
        return _run_api_sync(lambda db: full_backfill(db, limit_per_location=limit_per_location))

    return run_reported_sync("s95 API: полный backfill", _run)


@celery_app.task(name="s95_sync.fetch_protocol_from_profile", queue="s95")
def fetch_protocol_from_profile_task(
    location_slug: str,
    event_date_iso: str,
    location_name: str | None = None,
    force: bool = False,
) -> dict[str, object]:
    from datetime import date

    from app.sync.profile_protocol_queue import fetch_s95_protocol_for_profile

    db = get_session_factory()()
    try:
        fetch_s95_protocol_for_profile(
            db,
            location_slug=location_slug,
            event_date=date.fromisoformat(event_date_iso),
            location_name=location_name or location_slug,
            force=force,
        )
        return {
            "location_slug": location_slug,
            "event_date": event_date_iso,
            "status": "ok",
        }
    except Exception as exc:
        db.rollback()
        return {
            "location_slug": location_slug,
            "event_date": event_date_iso,
            "status": "error",
            "error": str(exc),
        }
    finally:
        db.close()
