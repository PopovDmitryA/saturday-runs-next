from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.leaderboard_service import LEADERBOARD_METRICS, refresh_leaderboard_cache
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# Очередь runpark: её воркер самый свободный (5 коротких синков в день) и не
# обслуживает user-очереди, так что долгий пересчёт не задержит пользовательский sync.
@celery_app.task(name="leaderboards.warm_cache", queue="runpark")
def warm_leaderboards_cache() -> dict[str, object]:
    """Пересчитывает и перезаписывает кэш всех рейтингов, не дожидаясь TTL.

    Без прогрева первый посетитель раздела после протухания кэша ждал бы
    расчёт (30+ секунд на метрику) прямо в запросе.
    """
    db = get_session_factory()()
    results: dict[str, object] = {}
    try:
        for metric in LEADERBOARD_METRICS:
            try:
                snapshot = refresh_leaderboard_cache(db, metric)
                results[metric] = snapshot.get("entrants", 0)
            except Exception:
                logger.exception("leaderboards warm failed for metric %s", metric)
                db.rollback()
                results[metric] = "error"
    finally:
        db.close()
    logger.info("leaderboards cache warmed: %s", results)
    return results
