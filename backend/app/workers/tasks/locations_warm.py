from __future__ import annotations

import logging
from typing import Any, cast

from app.db.session import get_session_factory
from app.services.location_page_service import (
    build_last_results,
    build_location_events,
    build_location_leaders,
    build_location_page,
    build_locations_index,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# Очередь runpark — как у leaderboards.warm_cache: её воркер самый свободный
# и не обслуживает user-очереди, так что долгий прогрев не задержит
# пользовательский sync.
@celery_app.task(name="locations.warm_cache", queue="runpark")
def warm_locations_cache() -> dict[str, object]:
    """Пересчитывает кэш каталога и страниц локаций, не дожидаясь TTL.

    Без прогрева первый посетитель после протухания кэша ждал бы расчёт
    прямо в запросе: каталог — агрегация по всем событиям всех платформ,
    страница локации — полтора десятка запросов к БД.
    """
    db = get_session_factory()()
    warmed = 0
    failed = 0
    try:
        # refresh=True — считаем заново и перезаписываем; иначе прогрев прочитал
        # бы ещё живой кэш и ничего не обновил. Раньше здесь стоял
        # use_cache=False, но это «не читать И не писать»: прогрев считал всё
        # впустую, кэш наполняли сами посетители ценой холодного расчёта.
        index = build_locations_index(db, refresh=True)
        build_last_results(db, refresh=True)
        items = cast(list[dict[str, Any]], index.get("items") or [])
        for item in items:
            slug = item.get("slug")
            if not slug:
                continue
            try:
                build_location_page(db, str(slug), refresh=True)
                build_location_events(db, str(slug), refresh=True)
                build_location_leaders(db, str(slug), refresh=True)
                warmed += 1
            except Exception:
                logger.exception("locations warm failed for slug %s", slug)
                db.rollback()
                failed += 1
    finally:
        db.close()
    result: dict[str, object] = {"warmed": warmed, "failed": failed}
    logger.info("locations cache warmed: %s", result)
    return result
