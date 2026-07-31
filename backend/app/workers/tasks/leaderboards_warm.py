from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.leaderboard_service import (
    GENDERED_METRICS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    MIN_VISITS_METRICS,
    PLATFORM_FILTER_METRICS,
    PLATFORM_FILTER_VALUES,
    refresh_leaderboard_cache,
)
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
    # Абсолют у всех метрик + разрез М/Ж у победных (wins/win_locations) +
    # пороги визитов 2..5 и фильтр по системе у рейтинга туризма (варианты «от
    # 1»/«все системы» уже в списке абсолютов): каждая кнопка фильтра — свой
    # снапшот, без прогрева первый клик по ней ждал бы полный пересчёт.
    # Комбинацию «своя система» × «свой порог визитов» не прогреваем — это
    # 16 дополнительных полных пересчётов ради редкого сочетания двух фильтров
    # разом; посетитель, который дойдёт до неё, один раз подождёт расчёт (тот же
    # компромисс, что и у непрогретого кэша вообще).
    variants = [(metric, "all", 1, "all") for metric in LEADERBOARD_METRICS]
    variants += [
        (metric, gender, 1, "all")
        for metric in GENDERED_METRICS
        for gender in ("male", "female")
    ]
    variants += [
        (metric, "all", visits, "all")
        for metric in MIN_VISITS_METRICS
        for visits in range(2, MAX_MIN_VISITS + 1)
    ]
    variants += [
        (metric, "all", 1, platform)
        for metric in PLATFORM_FILTER_METRICS
        for platform in PLATFORM_FILTER_VALUES
        if platform != "all"
    ]
    try:
        for metric, gender, min_visits, platform in variants:
            key = metric if gender == "all" else f"{metric}:{gender}"
            if min_visits > 1:
                key = f"{key}:v{min_visits}"
            if platform != "all":
                key = f"{key}:p{platform}"
            try:
                snapshot = refresh_leaderboard_cache(db, metric, gender, min_visits, platform)
                results[key] = snapshot.get("entrants", 0)
            except Exception:
                logger.exception("leaderboards warm failed for %s", key)
                db.rollback()
                results[key] = "error"
    finally:
        db.close()
    logger.info("leaderboards cache warmed: %s", results)
    return results
