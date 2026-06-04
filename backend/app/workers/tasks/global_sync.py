"""Backward-compatible Celery task names (routed to five_verst queue)."""

from __future__ import annotations

from app.workers.celery_app import celery_app
from app.workers.tasks import five_verst_sync as fv


@celery_app.task(name="global_sync.sync_location", queue="five_verst")
def sync_location_task(
    location_slug: str,
    summaries_limit: int | None = None,
    protocol_fetch_limit: int = 3,
) -> dict[str, object]:
    return fv.sync_location_task.run(
        location_slug=location_slug,
        summaries_limit=summaries_limit,
        protocol_fetch_limit=protocol_fetch_limit,
    )


@celery_app.task(name="global_sync.sync_locations_registry", queue="five_verst")
def sync_locations_registry_task(limit: int | None = None) -> dict[str, object]:
    return fv.sync_locations_registry_task.run(limit=limit)


@celery_app.task(name="global_sync.sync_all_location_summaries", queue="five_verst")
def sync_all_location_summaries() -> dict[str, object]:
    return fv.enqueue_all_location_summaries.run()


@celery_app.task(name="global_sync.sync_recent_protocols", queue="five_verst")
def sync_recent_protocols(protocol_fetch_limit: int = 3) -> dict[str, object]:
    del protocol_fetch_limit
    return fv.enqueue_recent_protocols.run()
