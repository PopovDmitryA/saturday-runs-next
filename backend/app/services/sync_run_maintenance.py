from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import SyncRun, SyncRunStatus

logger = logging.getLogger(__name__)

_STALE_AFTER: dict[str, timedelta] = {
    "five_verst:latest": timedelta(hours=2),
    "s95:latest": timedelta(hours=2),
    "five_verst:reconcile_protocols": timedelta(hours=3),
    "s95:reconcile_protocols": timedelta(hours=3),
    "s95:athletes_registry": timedelta(hours=6),
}

_DEFAULT_STALE = timedelta(hours=4)
_STALE_MESSAGE = "Прервано: превышено время ожидания (вероятно рестарт воркера)"


def stale_after(sync_type: str) -> timedelta:
    if sync_type in _STALE_AFTER:
        return _STALE_AFTER[sync_type]
    if sync_type.startswith("five_verst:location:") or sync_type.startswith("s95:location:"):
        return timedelta(hours=2)
    return _DEFAULT_STALE


def close_stale_sync_runs(db: Session, *, now: datetime | None = None) -> int:
    """Mark long-running sync_runs as failed so admin UI reflects reality."""
    current = now or datetime.now(timezone.utc)
    running = db.query(SyncRun).filter(SyncRun.status == SyncRunStatus.running).all()
    closed = 0
    for run in running:
        started = run.started_at
        if started is None:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = current - started.astimezone(timezone.utc)
        if age <= stale_after(run.sync_type):
            continue
        run.status = SyncRunStatus.failed
        run.finished_at = current
        run.error_message = _STALE_MESSAGE
        closed += 1
        logger.warning(
            "Closed stale sync_run %s (%s, age %s)",
            run.id,
            run.sync_type,
            age,
        )
    if closed:
        db.commit()
    return closed
