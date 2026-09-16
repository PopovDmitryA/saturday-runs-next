"""Backward-compatible Celery task names (фоновая очередь 5 вёрст)."""

from __future__ import annotations

from app.workers.celery_app import celery_app
from app.workers.queues import FIVE_VERST_BATCH_QUEUE
from app.workers.tasks import five_verst_sync as fv


@celery_app.task(name="global_sync.sync_location", queue=FIVE_VERST_BATCH_QUEUE)
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


@celery_app.task(name="global_sync.sync_locations_registry", queue=FIVE_VERST_BATCH_QUEUE)
def sync_locations_registry_task(limit: int | None = None) -> dict[str, object]:
    return fv.sync_locations_registry_task.run(limit=limit)


@celery_app.task(name="global_sync.sync_all_location_summaries", queue=FIVE_VERST_BATCH_QUEUE)
def sync_all_location_summaries() -> dict[str, object]:
    return fv.enqueue_all_location_summaries.run()


@celery_app.task(name="global_sync.sync_recent_protocols", queue=FIVE_VERST_BATCH_QUEUE)
def sync_recent_protocols(protocol_fetch_limit: int = 3) -> dict[str, object]:
    del protocol_fetch_limit
    return fv.enqueue_recent_protocols.run()
