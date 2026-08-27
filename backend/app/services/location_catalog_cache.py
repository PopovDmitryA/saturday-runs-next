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

from sqlalchemy.orm import Session

from app.services.location_page_service import (
    invalidate_last_results_cache,
    invalidate_location_page_cache,
    invalidate_locations_index_cache,
    resolve_location_identity,
)

logger = logging.getLogger(__name__)


def flush_location_catalog_caches(reason: str) -> None:
    """Погасить снимки каталога локаций. Redis недоступен — не беда: витрины
    просто доживут до TTL, ронять из-за этого синк незачем."""
    try:
        invalidate_locations_index_cache()
        invalidate_last_results_cache()
    except Exception:  # noqa: BLE001 — сброс кэша не должен ронять синк
        logger.warning("Не удалось сбросить кэш каталога локаций (%s)", reason, exc_info=True)
        return
    logger.info("Кэш каталога локаций сброшен: %s", reason)


def flush_location_page_caches(db: Session, slugs: list[str], reason: str) -> None:
    """Погасить кэш страниц перечисленных площадок (TTL там три часа).

    Гасим и слаг системы, и слаг идентичности: страница резолвит любой из них,
    а ключ кэша строится по тому, что попросили. Нужно там, где изменение
    видно прямо на странице — например, отмена ближайшего старта: ждать три
    часа с такой новостью бессмысленно.
    """
    for slug in slugs:
        try:
            invalidate_location_page_cache(slug)
            identity = resolve_location_identity(db, slug)
            if identity is not None and identity.slug != slug:
                invalidate_location_page_cache(identity.slug)
        except Exception:  # noqa: BLE001 — сброс кэша не должен ронять синк
            logger.warning("Не удалось сбросить кэш страницы локации %s (%s)", slug, reason, exc_info=True)
