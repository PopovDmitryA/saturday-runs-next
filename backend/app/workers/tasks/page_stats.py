from __future__ import annotations

import logging

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.page_analytics_service import cleanup_old_events, rollup_recent_days
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="page_stats.rollup")
def rollup_task() -> dict[str, object]:
    """Пересборка дневных агрегатов page_stats_daily за сегодня и вчера (МСК)
    + удаление сырых событий старше retention. Идемпотентна, ходит каждый час:
    страница «Популярность» читает только агрегаты, лаг данных — до часа."""
    settings = get_settings()
    db = get_session_factory()()
    try:
        groups = rollup_recent_days(db, days=2)
        deleted = cleanup_old_events(db, retention_days=settings.page_events_retention_days)
        if deleted:
            logger.info("page_stats: удалено %s сырых событий старше %s дней", deleted, settings.page_events_retention_days)
        return {"ok": True, "groups": groups, "deleted_events": deleted}
    finally:
        db.close()
