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

from app.core.redis_client import get_redis_client
from app.services.location_page_service import (
    invalidate_last_results_cache,
    invalidate_location_page_cache,
    invalidate_locations_index_cache,
    resolve_location_identity,
)
from app.services.location_regions_rating_service import invalidate_regions_rating_cache

logger = logging.getLogger(__name__)


WARM_DEBOUNCE_KEY = "locations:catalog-warm:pending"
WARM_DEBOUNCE_SECONDS = 5 * 60
WARM_COUNTDOWN_SECONDS = 30


def _schedule_catalog_warm(reason: str) -> bool:
    """Поставить прогрев каталога в очередь warm после сброса снимков.

    Без прогрева пустой ключ каталога оплачивает первый же посетитель: сборка
    индекса — агрегация по всем событиям всех систем, 5–15 с прямо в запросе
    (аудит 13.09.2026, QRY-LOCATIONS-01). Считать здесь же, синхронно, нельзя:
    правило молчания зовёт сброс ДО коммита (locations_status.py), и свежая
    сессия записала бы в кэш ещё старые статусы. Поэтому задача, и с
    countdown — к её старту транзакция синка уже закрыта.

    Дебаунс на 5 минут: за один прогон синка сброс может случиться несколько
    раз (реестр + отмены), а прогрев тяжёлый и нужен один.
    """
    try:
        from app.workers.tasks.locations_warm import warm_locations_cache

        redis_client = get_redis_client()
        if not redis_client.set(WARM_DEBOUNCE_KEY, reason, nx=True, ex=WARM_DEBOUNCE_SECONDS):
            logger.info("Прогрев каталога уже запланирован, пропускаем (%s)", reason)
            return False
        warm_locations_cache.apply_async(countdown=WARM_COUNTDOWN_SECONDS)
    except Exception:  # noqa: BLE001 — брокер недоступен: витрина просто пересчитается в запросе
        logger.warning("Не удалось запланировать прогрев каталога (%s)", reason, exc_info=True)
        return False
    return True


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
    warm_scheduled = _schedule_catalog_warm(reason)
    logger.info(
        "Кэш каталога локаций сброшен: %s (прогрев %s)",
        reason,
        "запланирован" if warm_scheduled else "не запланирован",
    )


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
