from __future__ import annotations

import logging
from dataclasses import asdict
from uuid import UUID

from app.db.session import get_session_factory
from app.models import SyncJob, SyncJobTrigger
from app.services.sync_job_service import fail_sync_job
from app.services.sync_run_params import (
    s95_athletes_registry_details,
    s95_latest_details,
    s95_reconcile_details,
    s95_registry_details,
    s95_rotation_details,
)
from app.sync.s95_athletes_registry import S95AthletesRegistrySyncOptions, sync_s95_athletes_registry
from app.sync.s95_global_sync import S95LocationSyncOptions, configured_s95_locations, sync_s95_location
from app.sync.s95_latest import S95LatestSyncOptions, sync_s95_latest
from app.sync.s95_location_rotation import sync_next_s95_location_batch
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry
from app.sync.s95_reconcile import ReconcileProtocolsOptions, reconcile_stale_protocols
from app.sync.s95_user_sync import run_s95_user_sync
from app.s95.fetch.priority import s95_user_sync_context
from app.workers.celery_app import celery_app
from app.workers.s95_batch_yield import run_s95_batch_reported_sync

logger = logging.getLogger(__name__)


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
    name = f"s95 location {location_external_key}"

    location_kwargs = {
        "location_external_key": location_external_key,
        "location_name": location_name,
        "location_source_url": location_source_url,
        "summaries_limit": summaries_limit,
        "protocol_fetch_limit": protocol_fetch_limit,
    }

    def _run() -> dict[str, object]:
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

    return run_s95_batch_reported_sync(
        name,
        _run,
        reenqueue=lambda: s95_sync_location_task.apply_async(kwargs=location_kwargs, queue="s95"),
    )


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


@celery_app.task(name="s95_sync.sync_location_rotation", queue="s95")
def s95_sync_location_rotation_task(*, force: bool = False) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    name = "s95 location rotation"
    details = s95_rotation_details(
        summaries_limit=settings.s95_location_batch_summaries_limit,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_next_s95_location_batch(db)
            payload: dict[str, object] = {
                "location_external_key": result.location_external_key,
                "rotation_index": result.rotation_index,
                "locations_total": result.locations_total,
                "errors": result.errors,
            }
            if result.sync is not None:
                payload.update(
                    {
                        "summaries_total": result.sync.summaries_total,
                        "summaries_upserted": result.sync.summaries_upserted,
                        "protocols_fetched": result.sync.protocols_fetched,
                    }
                )
            return payload
        finally:
            db.close()

    return run_s95_batch_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="s95:rotation",
        force=force,
        reenqueue=lambda: s95_sync_location_rotation_task.apply_async(kwargs={"force": force}, queue="s95"),
    )


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


@celery_app.task(name="s95_sync.sync_latest", queue="s95")
def s95_sync_latest_task(
    protocol_fetch_limit: int | None = None,
    update_limit: int | None = None,
    *,
    force: bool = False,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if protocol_fetch_limit is None:
        protocol_fetch_limit = settings.s95_sync_protocol_limit
    if update_limit is None:
        update_limit = settings.s95_sync_latest_update_limit
    name = "s95 latest /activities"
    details = s95_latest_details(
        update_limit=update_limit,
        protocol_fetch_limit=protocol_fetch_limit,
        fetch_all_protocols_on_change=settings.s95_fetch_all_protocols_on_change,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_s95_latest(
                db,
                S95LatestSyncOptions(
                    protocol_fetch_limit=protocol_fetch_limit,
                    update_limit=update_limit,
                    fetch_all_protocols_on_change=settings.s95_fetch_all_protocols_on_change,
                ),
            )
            return asdict(result)
        finally:
            db.close()

    return run_s95_batch_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="s95:latest",
        force=force,
        reenqueue=lambda: s95_sync_latest_task.apply_async(
            kwargs={
                "protocol_fetch_limit": protocol_fetch_limit,
                "update_limit": update_limit,
                "force": force,
            },
            queue="s95",
        ),
    )


@celery_app.task(name="s95_sync.enqueue_latest", queue="s95")
def s95_enqueue_latest() -> dict[str, object]:
    s95_sync_latest_task.apply_async(kwargs={"force": True}, queue="s95")
    return {"enqueued": 1}


@celery_app.task(name="s95_sync.reconcile_stale_protocols", queue="s95")
def s95_reconcile_stale_protocols_task(
    limit: int | None = None,
    min_check_interval_days: int | None = None,
    location_slug: str | None = None,
    *,
    force: bool = False,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if limit is None:
        limit = settings.s95_reconcile_batch_limit
    if min_check_interval_days is None:
        min_check_interval_days = settings.s95_reconcile_min_check_interval_days
    name = "s95 reconcile protocols"
    details = s95_reconcile_details(
        limit=limit,
        min_check_interval_days=min_check_interval_days,
        location_slug=location_slug,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = reconcile_stale_protocols(
                db,
                ReconcileProtocolsOptions(
                    limit=limit,
                    min_check_interval_days=min_check_interval_days,
                    location_slug=location_slug,
                ),
            )
            return asdict(result)
        finally:
            db.close()

    return run_s95_batch_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="s95:reconcile",
        force=force,
        reenqueue=lambda: s95_reconcile_stale_protocols_task.apply_async(
            kwargs={
                "limit": limit,
                "min_check_interval_days": min_check_interval_days,
                "location_slug": location_slug,
                "force": force,
            },
            queue="s95",
        ),
    )


@celery_app.task(name="s95_sync.enqueue_reconcile_protocols", queue="s95")
def s95_enqueue_reconcile_protocols() -> dict[str, object]:
    s95_reconcile_stale_protocols_task.apply_async(kwargs={"force": True}, queue="s95")
    return {"enqueued": 1}


@celery_app.task(name="s95_sync.sync_athletes_registry", queue="s95")
def s95_sync_athletes_registry_task(limit: int | None = None, *, force: bool = False) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if limit is None:
        limit = settings.s95_athletes_registry_batch_limit
    name = "s95 athletes registry"
    details = s95_athletes_registry_details(limit=limit)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_s95_athletes_registry(
                db,
                S95AthletesRegistrySyncOptions(limit=limit),
            )
            return asdict(result)
        finally:
            db.close()

    return run_s95_batch_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="s95:athletes",
        force=force,
        reenqueue=lambda: s95_sync_athletes_registry_task.apply_async(
            kwargs={"limit": limit, "force": force},
            queue="s95",
        ),
    )


@celery_app.task(name="s95_sync.enqueue_athletes_registry", queue="s95")
def s95_enqueue_athletes_registry() -> dict[str, object]:
    s95_sync_athletes_registry_task.apply_async(kwargs={"force": True}, queue="s95")
    return {"enqueued": 1}


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
