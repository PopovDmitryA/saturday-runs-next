from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.portal_home_service import warm_portal_home_cache
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="portal_cache.warm_home")
def warm_home_task() -> dict[str, object]:
    """Плановый прогрев Redis-кэша главной портала — держит TTL живым, чтобы
    ни один запрос не попадал на холодный пересчёт (~2 мин на проде)."""
    db = get_session_factory()()
    try:
        warm_portal_home_cache(db)
        return {"ok": True}
    finally:
        db.close()
