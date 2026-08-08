from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from app.db.session import get_session_factory
from app.services.sync_run_params import (
    five_verst_clubs_details_details,
    five_verst_clubs_registry_details,
    five_verst_latest_details,
    five_verst_location_details,
    five_verst_reconcile_details,
    five_verst_registry_details,
    five_verst_rotation_details,
)
from app.sync.five_verst_clubs import ClubsRegistrySyncOptions, sync_club_details_batch, sync_clubs_registry
from app.sync.five_verst_latest import LatestResultsSyncOptions, sync_latest_results
from app.sync.five_verst_location_rotation import sync_next_location_batch
from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry
from app.sync.five_verst_reconcile import ReconcileProtocolsOptions, reconcile_stale_protocols
from app.sync.global_sync import LocationSyncOptions, sync_location, sync_location_summaries_only
from app.workers.celery_app import celery_app
from app.workers.tasks.sync_task_reporting import run_reported_sync

logger = logging.getLogger(__name__)


def _schedule_dashboard_warm(started_at: datetime) -> None:
    """Отдать прогрев дашбордов в фоновую задачу.

    Синк знает только момент своего старта; кого именно затронули новые
    протоколы — разбирается dashboard_warm по run_results.fetched_at. Раньше
    здесь сносился кэш всем пользователям платформы, и пересчёт доставался
    первому зашедшему в профиль.
    """
    from app.workers.tasks.dashboard_warm import schedule_dashboard_warm

    schedule_dashboard_warm(started_at)


def _protocol_limit(settings) -> int | None:
    return settings.five_verst_sync_protocol_limit


def _latest_update_limit(settings) -> int | None:
    return settings.five_verst_sync_latest_update_limit


@celery_app.task(name="five_verst_sync.sync_location", queue="five_verst")
def sync_location_task(
    location_slug: str,
    summaries_limit: int | None = None,
    protocol_fetch_limit: int | None = None,
    fetch_all_protocols_on_change: bool | None = None,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if protocol_fetch_limit is None:
        protocol_fetch_limit = _protocol_limit(settings)
    if fetch_all_protocols_on_change is None:
        fetch_all_protocols_on_change = settings.five_verst_fetch_all_protocols_on_change
    name = f"5v location {location_slug}"
    details = five_verst_location_details(
        location_slug=location_slug,
        summaries_limit=summaries_limit,
        protocol_fetch_limit=protocol_fetch_limit,
        fetch_all_protocols_on_change=fetch_all_protocols_on_change,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            started_at = datetime.now(timezone.utc)
            result = sync_location(
                db,
                LocationSyncOptions(
                    location_slug=location_slug,
                    summaries_limit=summaries_limit,
                    protocol_fetch_limit=protocol_fetch_limit,
                    fetch_all_protocols_on_change=fetch_all_protocols_on_change,
                ),
            )
            if result.run_results_upserted > 0:
                db.commit()
                _schedule_dashboard_warm(started_at)
            return {
                "location_slug": result.location_slug,
                "summaries_total": result.summaries_total,
                "summaries_upserted": result.summaries_upserted,
                "summaries_unchanged": result.summaries_unchanged,
                "protocols_fetched": result.protocols_fetched,
                "fetched_protocols": result.fetched_protocols,
                "changed_protocols": result.changed_protocols,
                "run_results_upserted": result.run_results_upserted,
                "volunteer_results_upserted": result.volunteer_results_upserted,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(name, _run, details=details, batch_queue_name="five_verst")


@celery_app.task(name="five_verst_sync.sync_location_summaries", queue="five_verst")
def sync_location_summaries_task(
    location_slug: str,
    summaries_limit: int | None = None,
) -> dict[str, object]:
    name = f"5v summaries {location_slug}"

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_location_summaries_only(db, location_slug, summaries_limit=summaries_limit)
            return {
                "location_slug": result.location_slug,
                "summaries_total": result.summaries_total,
                "summaries_upserted": result.summaries_upserted,
                "summaries_unchanged": result.summaries_unchanged,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(name, _run, batch_queue_name="five_verst")


@celery_app.task(name="five_verst_sync.sync_locations_registry", queue="five_verst")
def sync_locations_registry_task(limit: int | None = None, *, force: bool = False) -> dict[str, object]:
    name = "5v registry /events/"
    details = five_verst_registry_details(limit=limit)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_locations_registry(db, LocationRegistrySyncOptions(limit=limit))
            return {
                "entries_total": result.entries_total,
                "locations_updated": result.locations_updated,
                "locations_created": result.locations_created,
                "locations_skipped_no_coords": result.locations_skipped_no_coords,
                "coords_fetched": result.coords_fetched,
                "pause_status_changed": result.pause_status_changed,
                "cancel_status_changed": result.cancel_status_changed,
                "merge_requests_created": result.merge_requests_created,
                "merge_notifications_sent": result.merge_notifications_sent,
                "updated_locations": result.updated_locations,
                "created_locations": result.created_locations,
                "coords_fetched_locations": result.coords_fetched_locations,
                "pause_changed_locations": result.pause_changed_locations,
                "cancel_changed_locations": result.cancel_changed_locations,
                "merge_request_locations": result.merge_request_locations,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="five_verst:registry",
        force=force,
        batch_queue_name="five_verst",
    )


@celery_app.task(name="five_verst_sync.sync_latest_results", queue="five_verst")
def sync_latest_results_task(
    update_limit: int | None = None,
    protocol_fetch_limit: int | None = None,
    *,
    force: bool = False,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if update_limit is None:
        update_limit = _latest_update_limit(settings)
    if protocol_fetch_limit is None:
        protocol_fetch_limit = _protocol_limit(settings)
    name = "5v latest /results/latest/"
    details = five_verst_latest_details(
        update_limit=update_limit,
        protocol_fetch_limit=protocol_fetch_limit,
        fetch_all_protocols_on_change=settings.five_verst_fetch_all_protocols_on_change,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            started_at = datetime.now(timezone.utc)
            result = sync_latest_results(
                db,
                LatestResultsSyncOptions(
                    update_limit=update_limit,
                    protocol_fetch_limit=protocol_fetch_limit,
                    fetch_all_protocols_on_change=settings.five_verst_fetch_all_protocols_on_change,
                ),
            )
            if result.run_results_upserted > 0:
                db.commit()
                _schedule_dashboard_warm(started_at)
            return {
                "summaries_total": result.summaries_total,
                "needs_update": result.needs_update,
                "summaries_upserted": result.summaries_upserted,
                "new_summaries": result.new_summaries,
                "changed_summaries": result.changed_summaries,
                "missing_protocol": result.missing_protocol,
                "protocols_fetched": result.protocols_fetched,
                "fetched_protocols": result.fetched_protocols,
                "changed_protocols": result.changed_protocols,
                "run_results_upserted": result.run_results_upserted,
                "volunteer_results_upserted": result.volunteer_results_upserted,
                "planned_protocols": len(result.planned_protocols),
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="five_verst:latest",
        force=force,
        batch_queue_name="five_verst",
    )


@celery_app.task(name="five_verst_sync.sync_location_rotation", queue="five_verst")
def sync_location_rotation_task(*, force: bool = False) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    name = "5v location rotation"
    details = five_verst_rotation_details(
        summaries_limit=settings.five_verst_location_batch_summaries_limit,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            started_at = datetime.now(timezone.utc)
            result = sync_next_location_batch(db)
            if result.sync is not None and result.sync.run_results_upserted > 0:
                db.commit()
                _schedule_dashboard_warm(started_at)
            payload: dict[str, Any] = {
                "location_slug": result.location_slug,
                "rotation_index": result.rotation_index,
                "locations_total": result.locations_total,
                "errors": result.errors,
            }
            if result.sync is not None:
                payload.update(
                    {
                        "summaries_total": result.sync.summaries_total,
                        "summaries_upserted": result.sync.summaries_upserted,
                        "summaries_unchanged": result.sync.summaries_unchanged,
                        "protocols_fetched": result.sync.protocols_fetched,
                        "fetched_protocols": result.sync.fetched_protocols,
                        "changed_protocols": result.sync.changed_protocols,
                        "run_results_upserted": result.sync.run_results_upserted,
                        "volunteer_results_upserted": result.sync.volunteer_results_upserted,
                    }
                )
            return payload
        finally:
            db.close()

    return run_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="five_verst:rotation",
        force=force,
        batch_queue_name="five_verst",
    )


@celery_app.task(name="five_verst_sync.enqueue_all_location_summaries", queue="five_verst")
def enqueue_all_location_summaries() -> dict[str, object]:
    from app.platform_adapters.five_verst import bulk_parser

    slugs = bulk_parser.list_location_slugs()
    for slug in slugs:
        sync_location_summaries_task.apply_async(kwargs={"location_slug": slug}, queue="five_verst")
    return {"enqueued": len(slugs)}


@celery_app.task(name="five_verst_sync.enqueue_recent_protocols", queue="five_verst")
def enqueue_recent_protocols() -> dict[str, object]:
    from app.config import get_settings
    from app.platform_adapters.five_verst import bulk_parser

    settings = get_settings()
    slugs = bulk_parser.list_location_slugs()
    for slug in slugs:
        sync_location_task.apply_async(
            kwargs={
                "location_slug": slug,
                "summaries_limit": 5,
                "protocol_fetch_limit": settings.five_verst_sync_protocol_limit,
            },
            queue="five_verst",
        )
    return {"enqueued": len(slugs)}


@celery_app.task(name="five_verst_sync.enqueue_locations_registry", queue="five_verst")
def enqueue_locations_registry() -> dict[str, object]:
    sync_locations_registry_task.apply_async(queue="five_verst")
    return {"enqueued": 1}


@celery_app.task(name="five_verst_sync.enqueue_latest_results", queue="five_verst")
def enqueue_latest_results() -> dict[str, object]:
    sync_latest_results_task.apply_async(kwargs={"force": True}, queue="five_verst")
    return {"enqueued": 1}


@celery_app.task(name="five_verst_sync.reconcile_stale_protocols", queue="five_verst")
def reconcile_stale_protocols_task(
    limit: int | None = None,
    min_check_interval_days: int | None = None,
    location_slug: str | None = None,
    *,
    force: bool = False,
) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if limit is None:
        limit = settings.five_verst_reconcile_batch_limit
    if min_check_interval_days is None:
        min_check_interval_days = settings.five_verst_reconcile_min_check_interval_days
    name = "5v reconcile protocols"
    details = five_verst_reconcile_details(
        limit=limit,
        min_check_interval_days=min_check_interval_days,
        location_slug=location_slug,
    )

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            started_at = datetime.now(timezone.utc)
            result = reconcile_stale_protocols(
                db,
                ReconcileProtocolsOptions(
                    limit=limit,
                    min_check_interval_days=min_check_interval_days,
                    location_slug=location_slug,
                ),
            )
            # Сверка переписывает уже существующие протоколы — позиции и метки
            # рекордов после неё меняются ровно так же, как после нового
            # протокола, поэтому дашборды тоже надо греть.
            if result.run_results_upserted > 0:
                db.commit()
                _schedule_dashboard_warm(started_at)
            return asdict(result)
        finally:
            db.close()

    return run_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="five_verst:reconcile",
        force=force,
        batch_queue_name="five_verst",
    )


@celery_app.task(name="five_verst_sync.enqueue_reconcile_protocols", queue="five_verst")
def enqueue_reconcile_protocols() -> dict[str, object]:
    reconcile_stale_protocols_task.apply_async(queue="five_verst")
    return {"enqueued": 1}


@celery_app.task(name="five_verst_sync.sync_clubs_registry", queue="five_verst")
def sync_clubs_registry_task(limit: int | None = None, *, force: bool = False) -> dict[str, object]:
    name = "5v clubs registry /clubs/"
    details = five_verst_clubs_registry_details(limit=limit)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_clubs_registry(db, ClubsRegistrySyncOptions(limit=limit))
            return {
                "entries_total": result.entries_total,
                "clubs_created": result.clubs_created,
                "clubs_updated": result.clubs_updated,
                "clubs_unchanged": result.clubs_unchanged,
                "clubs_deactivated": result.clubs_deactivated,
                "clubs_reactivated": result.clubs_reactivated,
                "catalog_links_created": result.catalog_links_created,
                "marked_for_detail_sync": result.marked_for_detail_sync,
                "created_clubs": result.created_clubs,
                "updated_clubs": result.updated_clubs,
                "deactivated_clubs": result.deactivated_clubs,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="five_verst:clubs_registry",
        force=force,
        batch_queue_name="five_verst",
    )


@celery_app.task(name="five_verst_sync.sync_club_details", queue="five_verst")
def sync_club_details_task(limit: int | None = None, *, force: bool = False) -> dict[str, object]:
    from app.config import get_settings

    settings = get_settings()
    if limit is None:
        limit = settings.five_verst_clubs_batch_limit
    name = "5v clubs details"
    details = five_verst_clubs_details_details(limit=limit)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_club_details_batch(db, limit=limit)
            return {
                "clubs_planned": result.clubs_planned,
                "clubs_synced": result.clubs_synced,
                "clubs_not_found": result.clubs_not_found,
                "members_seen": result.members_seen,
                "members_added": result.members_added,
                "members_deactivated": result.members_deactivated,
                "participants_created": result.participants_created,
                "members_skipped": result.members_skipped,
                "slug_change_alerts": result.slug_change_alerts,
                "synced_clubs": result.synced_clubs,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(
        name,
        _run,
        details=details,
        hour_slot_key="five_verst:clubs_details",
        force=force,
        batch_queue_name="five_verst",
    )


@celery_app.task(name="five_verst_sync.fetch_protocol_from_profile", queue="five_verst")
def fetch_protocol_from_profile_task(
    location_slug: str,
    event_date_iso: str,
    event_number: int | None = None,
    location_name: str | None = None,
) -> dict[str, object]:
    from datetime import date

    from app.sync.profile_protocol_queue import fetch_five_verst_protocol_for_profile

    db = get_session_factory()()
    try:
        fetch_five_verst_protocol_for_profile(
            db,
            location_slug=location_slug,
            event_date=date.fromisoformat(event_date_iso),
            event_number=event_number,
            location_name=location_name or location_slug,
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
