from __future__ import annotations

from collections.abc import Callable

from app.s95.fetch.priority import S95YieldForUserSync
from app.workers.tasks.sync_task_reporting import run_reported_sync


def run_s95_batch_reported_sync(
    name: str,
    fn: Callable[[], dict[str, object]],
    *,
    reenqueue: Callable[[], None],
    **kwargs: object,
) -> dict[str, object]:
    try:
        return run_reported_sync(name, fn, batch_queue_name="s95", **kwargs)
    except S95YieldForUserSync:
        reenqueue()
        return {
            "skipped": True,
            "reason": "yielded_for_user_sync",
            "errors": [],
        }
