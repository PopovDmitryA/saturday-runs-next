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
    # полная сетка «порог визитов × система» у рейтинга туризма: каждая
    # кнопка (и их сочетание) — свой снапшот в кэше, без прогрева первый
    # заход на него ждал бы полный пересчёт прямо в запросе (31.07.2026 —
    # именно так и вышло: «2+» × «5 вёрст» не прогревались, посетитель ждал
    # больше минуты). Комбинаций у туризма немного (5×5=25, замкнутый набор
    # кнопок, а не открытый диапазон), прогреть все — дешевле, чем оставлять
    # часть непрогретой.
    variants = [(metric, "all", 1, "all") for metric in LEADERBOARD_METRICS]
    variants += [
        (metric, gender, 1, "all")
        for metric in GENDERED_METRICS
        for gender in ("male", "female")
    ]
    # Комбинируются только метрики, у которых оба фильтра есть разом — сейчас
    # это всегда «locations», но пересечение вместо MIN_VISITS_METRICS не
    # даёт сетке молча раздуться, если один из фильтров позже достанется
    # ещё какой-то метрике без второго.
    tourism_metrics = set(MIN_VISITS_METRICS) & set(PLATFORM_FILTER_METRICS)
    variants += [
        (metric, "all", visits, platform)
        for metric in tourism_metrics
        for visits in range(1, MAX_MIN_VISITS + 1)
        for platform in PLATFORM_FILTER_VALUES
        if visits > 1 or platform != "all"
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
