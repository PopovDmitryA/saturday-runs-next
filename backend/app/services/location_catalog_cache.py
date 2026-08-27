"""Сброс кэшей, показывающих список локаций и их статусы.

Статус площадки меняется двумя путями — синком реестра системы и правилом
молчания, — а витрины (каталог, карта, «последние результаты») держат свои
снимки в Redis с TTL в часы. До 20.08.2026 инвалидацию никто не вызывал:
функция сброса существовала, но была мёртвой, и отмена ближайшего старта
доезжала до карты только к следующему протуханию кэша.

Вызывать после любых изменений состава локаций или их флагов: синки сами знают,
менялось ли что-то, поэтому сброс идёт по факту изменений, а не на каждый
прогон.
"""

from __future__ import annotations

import logging

from app.services.location_page_service import (
    invalidate_last_results_cache,
    invalidate_locations_index_cache,
)
from app.services.location_regions_rating_service import invalidate_regions_rating_cache

logger = logging.getLogger(__name__)


def flush_location_catalog_caches(reason: str) -> None:
    """Погасить снимки каталога локаций. Redis недоступен — не беда: витрины
    просто доживут до TTL, ронять из-за этого синк незачем."""
    try:
        invalidate_locations_index_cache()
        invalidate_last_results_cache()
        # Рейтинг регионов считает те же площадки, что каталог, и держит свой
        # снимок: без сброса новая локация доехала бы до него только к TTL.
        invalidate_regions_rating_cache()
    except Exception:  # noqa: BLE001 — сброс кэша не должен ронять синк
        logger.warning("Не удалось сбросить кэш каталога локаций (%s)", reason, exc_info=True)
        return
    logger.info("Кэш каталога локаций сброшен: %s", reason)
