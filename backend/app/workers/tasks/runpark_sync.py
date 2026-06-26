from __future__ import annotations

import logging
from datetime import date, timedelta

from app.db.session import get_session_factory
from app.sync.runpark_global_sync import sync_runpark_batch
from app.workers.celery_app import celery_app
from app.workers.tasks.sync_task_reporting import run_reported_sync

logger = logging.getLogger(__name__)


@celery_app.task(name="runpark_sync.sync_latest", queue="runpark")
def runpark_sync_latest() -> dict[str, object]:
    since = date.today() - timedelta(days=7)

    def _run() -> dict[str, object]:
        db = get_session_factory()()
        try:
            result = sync_runpark_batch(db, since)
            return {
                "since": since.isoformat(),
                "events_total": result.events_total,
                "events_upserted": result.events_upserted,
                "run_results_upserted": result.run_results_upserted,
                "volunteer_results_upserted": result.volunteer_results_upserted,
                "errors": result.errors,
            }
        finally:
            db.close()

    return run_reported_sync(
        "runpark latest",
        _run,
        details=f"since {since.isoformat()}",
    )
