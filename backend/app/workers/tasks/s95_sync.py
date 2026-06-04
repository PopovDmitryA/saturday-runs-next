from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from app.db.session import get_session_factory
from app.models import SyncJob, SyncJobTrigger
from app.services.sync_job_service import fail_sync_job
from app.sync.s95_global_sync import S95LocationSyncOptions, configured_s95_locations, sync_s95_location
from app.sync.s95_latest import S95LatestSyncOptions, sync_s95_latest
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry
from app.sync.s95_reconcile import ReconcileProtocolsOptions, reconcile_stale_protocols
from app.sync.s95_athletes_registry import S95AthletesRegistrySyncOptions, sync_s95_athletes_registry
from app.sync.s95_user_sync import run_s95_user_sync
from app.workers.celery_app import celery_app


@celery_app.task(name="s95_sync.run_user_sync", queue="s95")
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

        job = run_s95_user_sync(
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


@celery_app.task(name="s95_sync.sync_location", queue="s95")
def s95_sync_location_task(
    location_external_key: str,
    location_name: str,
    location_source_url: str,
    summaries_limit: int | None = None,
    protocol_fetch_limit: int = 3,
) -> dict[str, object]:
    db = get_session_factory()()
    try:
        result = sync_s95_location(
            db,
            S95LocationSyncOptions(
                location_external_key=location_external_key,
                location_name=location_name,
                location_source_url=location_source_url,
                summaries_limit=summaries_limit,
                protocol_fetch_limit=protocol_fetch_limit,
            ),
        )
        return {
            "location_external_key": result.location_external_key,
            "summaries_total": result.summaries_total,
            "summaries_upserted": result.summaries_upserted,
            "protocols_fetched": result.protocols_fetched,
            "errors": result.errors,
        }
    finally:
        db.close()


@celery_app.task(name="s95_sync.enqueue_global_pipeline", queue="s95")
def s95_enqueue_global_pipeline() -> dict[str, object]:
    """Enqueue per-location sync tasks on the s95 queue (processed linearly)."""
    locations = configured_s95_locations()
    enqueued = 0
    for loc in locations:
        s95_sync_location_task.apply_async(
            kwargs={
                "location_external_key": loc.location_external_key,
                "location_name": loc.location_name,
                "location_source_url": loc.location_source_url,
                "protocol_fetch_limit": loc.protocol_fetch_limit,
            },
            queue="s95",
        )
        enqueued += 1
    return {"enqueued": enqueued}


@celery_app.task(name="s95_sync.sync_locations_registry", queue="s95")
def s95_sync_locations_registry_task(limit: int | None = None) -> dict[str, object]:
    db = get_session_factory()()
    try:
        result = sync_s95_locations_registry(
            db,
            S95LocationRegistrySyncOptions(limit=limit),
        )
        return asdict(result)
    finally:
        db.close()


@celery_app.task(name="s95_sync.enqueue_locations_registry", queue="s95")
def s95_enqueue_locations_registry() -> dict[str, object]:
    s95_sync_locations_registry_task.apply_async(queue="s95")
    return {"enqueued": 1}


@celery_app.task(name="s95_sync.sync_latest", queue="s95")
def s95_sync_latest_task(
    protocol_fetch_limit: int | None = None,
    update_limit: int | None = None,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    db = get_session_factory()()
    try:
        result = sync_s95_latest(
            db,
            S95LatestSyncOptions(
                protocol_fetch_limit=(
                    settings.s95_sync_protocol_limit
                    if protocol_fetch_limit is None
                    else protocol_fetch_limit
                ),
                update_limit=update_limit,
                fetch_all_protocols_on_change=settings.s95_fetch_all_protocols_on_change,
            ),
        )
        return asdict(result)
    finally:
        db.close()


@celery_app.task(name="s95_sync.enqueue_latest", queue="s95")
def s95_enqueue_latest() -> dict[str, object]:
    s95_sync_latest_task.apply_async(queue="s95")
    return {"enqueued": 1}


@celery_app.task(name="s95_sync.reconcile_stale_protocols", queue="s95")
def s95_reconcile_stale_protocols_task(
    limit: int | None = None,
    min_check_interval_days: int | None = None,
    location_slug: str | None = None,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    db = get_session_factory()()
    try:
        result = reconcile_stale_protocols(
            db,
            ReconcileProtocolsOptions(
                limit=limit if limit is not None else settings.s95_reconcile_batch_limit,
                min_check_interval_days=(
                    min_check_interval_days
                    if min_check_interval_days is not None
                    else settings.s95_reconcile_min_check_interval_days
                ),
                location_slug=location_slug,
            ),
        )
        return asdict(result)
    finally:
        db.close()


@celery_app.task(name="s95_sync.enqueue_reconcile_protocols", queue="s95")
def s95_enqueue_reconcile_protocols() -> dict[str, object]:
    s95_reconcile_stale_protocols_task.apply_async(queue="s95")
    return {"enqueued": 1}


@celery_app.task(name="s95_sync.sync_athletes_registry", queue="s95")
def s95_sync_athletes_registry_task(limit: int | None = None) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    db = get_session_factory()()
    try:
        result = sync_s95_athletes_registry(
            db,
            S95AthletesRegistrySyncOptions(
                limit=limit if limit is not None else settings.s95_athletes_registry_batch_limit,
            ),
        )
        return asdict(result)
    finally:
        db.close()


@celery_app.task(name="s95_sync.enqueue_athletes_registry", queue="s95")
def s95_enqueue_athletes_registry() -> dict[str, object]:
    s95_sync_athletes_registry_task.apply_async(queue="s95")
    return {"enqueued": 1}
