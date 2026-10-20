"""Сверка с каталогом площадок parkrun раз в сутки — см. services/parkrun_catalog_service.py."""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.parkrun_catalog_service import run_catalog_sync
from app.workers.celery_app import celery_app
from app.workers.tasks.sync_task_reporting import run_reported_sync
from app.workers.time_limits import LIMITS_MEDIUM

logger = logging.getLogger(__name__)


@celery_app.task(name="parkrun_catalog.sync", **LIMITS_MEDIUM)
def sync_parkrun_catalog_task() -> dict[str, object]:
    if not get_settings().parkrun_catalog_sync_enabled:
        return {"status": "skipped", "reason": "PARKRUN_CATALOG_SYNC_ENABLED=false"}
    return run_reported_sync("parkrun catalog", lambda: run_catalog_sync(get_session_factory()))
