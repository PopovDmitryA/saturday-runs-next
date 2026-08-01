from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.leaderboard_service import (
    GENDERED_METRICS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    MIN_VISITS_METRICS,
    LeaderboardMetric,
    make_snapshot_source,
    platform_filter_values,
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
    # Прогреваем всю сетку кнопок каждого рейтинга: зачёт (абсолют/М/Ж) ×
    # порог визитов × система. Каждое сочетание — свой снапшот в кэше, и без
    # прогрева первый заход на него ждал бы полный пересчёт прямо в запросе
    # (31.07.2026 именно так и вышло: «2+» × «5 вёрст» у туризма не
    # прогревались, посетитель ждал больше минуты). Наборы кнопок замкнутые,
    # так что сетка конечная и предсказуемая.
    variants: list[tuple[LeaderboardMetric, str, int, str]] = []
    for metric in LEADERBOARD_METRICS:
        genders = ("all", "male", "female") if metric in GENDERED_METRICS else ("all",)
        visits_options = (
            range(1, MAX_MIN_VISITS + 1) if metric in MIN_VISITS_METRICS else range(1, 2)
        )
        for gender in genders:
            for visits in visits_options:
                for platform in platform_filter_values(metric, gender):
                    variants.append((metric, gender, visits, platform))

    # Один источник на всю задачу: сырые выборки и справочники читаются из базы
    # один раз на рейтинг, а не на каждое сочетание фильтров (фильтры
    # применяются в Python — см. _MetricSource). Иначе десятки вариантов
    # означали бы десятки полных сканирований протоколов.
    source = make_snapshot_source(db)
    current_metric: str | None = None
    try:
        for metric, gender, min_visits, platform in variants:
            if source is not None and metric != current_metric:
                # Сырые строки прошлого рейтинга больше не нужны — отпускаем их,
                # чтобы пик памяти остался как при расчёте одного снапшота.
                source.release()
                current_metric = metric
            key = metric if gender == "all" else f"{metric}:{gender}"
            if min_visits > 1:
                key = f"{key}:v{min_visits}"
            if platform != "all":
                key = f"{key}:p{platform}"
            try:
                snapshot = refresh_leaderboard_cache(
                    db, metric, gender, min_visits, platform, source
                )
                results[key] = snapshot.get("entrants", 0)
            except Exception:
                logger.exception("leaderboards warm failed for %s", key)
                db.rollback()
                # Откат транзакции обесценивает кэш источника (строки читались в
                # ней) — начинаем следующий рейтинг с чистого листа.
                if source is not None:
                    source.release()
                results[key] = "error"
    finally:
        db.close()
    logger.info("leaderboards cache warmed: %s", results)
    return results
