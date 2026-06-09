from __future__ import annotations

import logging
from collections.abc import Callable

from app.services.batch_queue_guard import batch_queue_has_capacity
from app.services.scheduled_sync_guard import release_hourly_sync_slot, try_claim_hourly_sync_slot
from app.services.vk_admin_notify import notify_sync_finished, notify_sync_started

logger = logging.getLogger(__name__)


def run_reported_sync(
    name: str,
    fn: Callable[[], dict[str, object]],
    *,
    details: str | None = None,
    hour_slot_key: str | None = None,
    force: bool = False,
    batch_queue_name: str | None = None,
    batch_queue_max_depth: int = 120,
) -> dict[str, object]:
    if batch_queue_name and not batch_queue_has_capacity(
        batch_queue_name,
        max_depth=batch_queue_max_depth,
    ):
        return {
            "skipped": True,
            "reason": "batch_queue_full",
            "errors": [],
        }

    if hour_slot_key and not try_claim_hourly_sync_slot(hour_slot_key, force=force):
        return {
            "skipped": True,
            "reason": "duplicate_hour_slot",
            "errors": [],
        }

    notify_sync_started(name, details=details)
    payload: dict[str, object] = {"errors": ["task did not complete"]}
    try:
        payload = fn()
    except Exception as exc:
        if hour_slot_key:
            release_hourly_sync_slot(hour_slot_key)
        payload = {"errors": [str(exc)]}
        raise
    finally:
        if not payload.get("skipped"):
            try:
                notify_sync_finished(name, payload)
            except Exception:
                logger.exception("Failed to send VK sync finished notification for %s", name)
    return payload
