from __future__ import annotations

from app.services.celery_queue_inspector import (
    FIVE_VERST_BATCH_QUEUE,
    S95_BATCH_QUEUE,
    get_queue_length,
)

DEFAULT_BATCH_QUEUE_MAX_DEPTH = 120


def batch_queue_has_capacity(
    queue_name: str,
    *,
    max_depth: int = DEFAULT_BATCH_QUEUE_MAX_DEPTH,
) -> bool:
    return get_queue_length(queue_name) < max_depth


def five_verst_batch_has_capacity(*, max_depth: int = DEFAULT_BATCH_QUEUE_MAX_DEPTH) -> bool:
    return batch_queue_has_capacity(FIVE_VERST_BATCH_QUEUE, max_depth=max_depth)


def s95_batch_has_capacity(*, max_depth: int = DEFAULT_BATCH_QUEUE_MAX_DEPTH) -> bool:
    return batch_queue_has_capacity(S95_BATCH_QUEUE, max_depth=max_depth)
