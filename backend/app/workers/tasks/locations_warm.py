from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, cast

from app.db.session import get_session_factory
from app.services.location_page_service import (
    build_last_results,
    build_location_events,
    build_location_leaders,
    build_location_page,
    build_locations_index,
)
from app.services.unified_protocol_service import (
    build_unified_protocol,
    latest_protocol_saturday,
    list_protocol_weeks,
    week_start_of,
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
    weeks_warmed = 0
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
        # Единый протокол: свежая неделя и предыдущая. Холодный расчёт недели
        # — это 12–16 тыс. строк со всей страны, и без прогрева его оплатил бы
        # первый же посетитель субботним вечером. Список недель тоже трогаем:
        # он и даёт стрелки «предыдущая/следующая».
        try:
            list_protocol_weeks(db, refresh=True)
            latest = latest_protocol_saturday(db)
            if latest is not None:
                for saturday in (latest, week_start_of(latest) - timedelta(days=2)):
                    build_unified_protocol(db, saturday, per_page=1, refresh=True)
                    weeks_warmed += 1
        except Exception:
            logger.exception("unified protocol warm failed")
            db.rollback()
            failed += 1
    finally:
        db.close()
    result: dict[str, object] = {"warmed": warmed, "failed": failed, "weeks": weeks_warmed}
    logger.info("locations cache warmed: %s", result)
    return result
