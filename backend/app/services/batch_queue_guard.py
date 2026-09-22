from __future__ import annotations

from app.services.celery_queue_inspector import (
    get_queue_length,
)

DEFAULT_BATCH_QUEUE_MAX_DEPTH = 120


def batch_queue_has_capacity(
    queue_name: str,
    *,
    max_depth: int = DEFAULT_BATCH_QUEUE_MAX_DEPTH,
) -> bool:
    return get_queue_length(queue_name) < max_depth
