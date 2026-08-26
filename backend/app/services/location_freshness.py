"""Свежесть витрин локации после прихода новых результатов.

Страница локации, её журнал, рейтинги, каталог и «Результаты последней
субботы» живут в Redis с TTL 3 часа (см. LOCATION_PAGE_CACHE_TTL_SECONDS):
пересчёт тяжёлый, а данные меняются раз в неделю — так рассуждали, пока
единственным источником новых результатов был недельный синк.

С личными синками это перестало быть правдой: человек жмёт «обновить», через
минуту видит субботнюю пробежку у себя в профиле, открывает страницу площадки —
а там прошлая суббота. Данные в БД одни, а витрины показывают разное время.

Поэтому синки не сбрасывают кэш, а **подрезают** ему TTL до минуты-двух:
витрина обновится сама почти сразу после записи, но массовый прогон (сотни
локаций за проход) не превратится в сотню тяжёлых пересчётов подряд — внутри
этого окна посетитель по-прежнему читает готовый снимок.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from uuid import UUID

import redis
from sqlalchemy import and_, or_
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.redis_client import get_redis_client
from app.models import Location, LocationCatalog, LocationCatalogLink
from app.services.location_catalog_service import normalize_location_slug
from app.services.location_page_service import (
    LAST_RESULTS_CACHE_KEY,
    LOCATIONS_INDEX_CACHE_KEY,
    location_events_cache_key,
    location_leaders_cache_key,
    location_page_cache_key,
)

logger = logging.getLogger(__name__)

# Сколько ещё живёт снимок витрины после записи новых результатов.
STALE_AFTER_WRITE_SECONDS = 60

# Каталог локаций и «последняя суббота» считаются по всем событиям всех систем
# (единицы секунд на прод-объёме) — им даём пожить подольше: субъективной
# рассинхронизации «профиль показывает, локация нет» они не создают, а
# пересчитывать их раз в минуту весь субботний синк дорого.
STALE_AFTER_WRITE_SECONDS_SHOWCASE = 2 * 60

# Ключи сессии: что подрезать после коммита и уже посчитанные слаги площадок.
_PENDING_KEYS = "location_freshness_pending_keys"
_PENDING_SHOWCASE_KEYS = "location_freshness_pending_showcase_keys"
_SLUG_MEMO = "location_freshness_slug_memo"


def identity_slugs_for_locations(db: Session, location_ids: Iterable[UUID]) -> set[str]:
    """Все слаги, под которыми открывается страница этих площадок.

    Страница у идентичности одна, а адресов у неё столько, сколько систем на
    площадке когда-либо работало (кэш пишется по запрошенному слагу). Сбрасывать
    надо все, иначе parkrun-адрес закрытой площадки останется со старым снимком.
    """
    location_ids = [location_id for location_id in location_ids if location_id is not None]
    if not location_ids:
        return set()

    rows = (
        db.query(Location.id, Location.platform_id, Location.external_key)
        .filter(Location.id.in_(location_ids))
        .all()
    )
    slugs = {row.external_key for row in rows if row.external_key}

    # Связка с каталожным узлом — либо прямая (location_id), либо по паре
    # «система + слаг»: у части связок location_id не проставлен.
    key_pairs = [
        and_(
            LocationCatalogLink.platform_id == row.platform_id,
            LocationCatalogLink.external_key == row.external_key,
        )
        for row in rows
        if row.external_key
    ]
    link_filter: ColumnElement[bool] = LocationCatalogLink.location_id.in_(location_ids)
    if key_pairs:
        link_filter = or_(link_filter, *key_pairs)
    catalog_ids = {
        catalog_id
        for (catalog_id,) in db.query(LocationCatalogLink.catalog_id).filter(link_filter).all()
    }
    if catalog_ids:
        slugs.update(
            external_key
            for (external_key,) in db.query(LocationCatalogLink.external_key)
            .filter(LocationCatalogLink.catalog_id.in_(catalog_ids))
            .all()
            if external_key
        )
        slugs.update(
            external_key
            for (external_key,) in db.query(Location.external_key)
            .join(LocationCatalogLink, LocationCatalogLink.location_id == Location.id)
            .filter(LocationCatalogLink.catalog_id.in_(catalog_ids))
            .all()
            if external_key
        )
        slugs.update(
            legacy_slug
            for (legacy_slug,) in db.query(LocationCatalog.legacy_parkrun_slug)
            .filter(LocationCatalog.id.in_(catalog_ids))
            .all()
            if legacy_slug
        )

    normalized: set[str] = set()
    for slug in slugs:
        cleaned = slug.strip().lower()
        if not cleaned:
            continue
        normalized.add(cleaned)
        # Кэш пишется по слагу из адреса, а страница открывается и по его
        # нормализованной форме — гасим обе.
        variant = normalize_location_slug(cleaned)
        if variant:
            normalized.add(variant)
    return normalized


def _cap_ttl(keys: Iterable[str], ttl_seconds: int) -> int:
    """Подрезать TTL перечисленных ключей до ttl_seconds. Возвращает сколько подрезали."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return 0
    client = get_redis_client()
    pipe = client.pipeline()
    for key in keys:
        pipe.ttl(key)
    ttls = pipe.execute()
    # TTL: -2 — ключа нет, -1 — вечный (у нас таких нет, но подрезать всё равно надо).
    stale = [key for key, ttl in zip(keys, ttls, strict=False) if ttl == -1 or ttl > ttl_seconds]
    if not stale:
        return 0
    pipe = client.pipeline()
    for key in stale:
        pipe.expire(key, ttl_seconds)
    pipe.execute()
    return len(stale)


def _slugs_with_memo(db: Session, location_ids: list[UUID]) -> set[str]:
    """То же, что identity_slugs_for_locations, но с памятью в пределах сессии.

    Массовый синк проходит по одной площадке десятками событий подряд — без
    памяти каждый протокол платил бы за три одинаковых запроса к каталогу.
    """
    memo: dict[UUID, frozenset[str]] = db.info.setdefault(_SLUG_MEMO, {})
    unknown = [location_id for location_id in location_ids if location_id not in memo]
    if unknown:
        resolved = identity_slugs_for_locations(db, unknown)
        # Слаги идентичности общие на всю группу — раскладывать их по конкретным
        # площадкам незачем, важно лишь не спрашивать каталог повторно.
        shared = frozenset(resolved)
        for location_id in unknown:
            memo[location_id] = shared
    slugs: set[str] = set()
    for location_id in location_ids:
        slugs.update(memo.get(location_id, frozenset()))
    return slugs


def mark_location_results_changed(
    db: Session,
    location_ids: Iterable[UUID],
    *,
    reason: str,
    protocols: Iterable[tuple[str, date]] = (),
) -> None:
    """Пометить витрины площадки устаревшими: у неё появились новые результаты.

    Реальная подрезка TTL — после коммита (см. слушатель ниже): до него данных
    для читателя ещё нет, и погашенный кэш просто перечитал бы старое.

    `protocols` — пары «система, дата старта» тех протоколов, которые менялись:
    у страницы протокола свой ключ кэша, и без этого исправленный протокол
    доезжал бы до читателя только к следующему протуханию.
    """
    ids = [location_id for location_id in location_ids if location_id is not None]
    if not ids:
        return
    try:
        slugs = _slugs_with_memo(db, ids)
    except Exception:  # noqa: BLE001 — свежесть витрин не должна ронять синк
        logger.warning("Не удалось собрать слаги локаций (%s)", reason, exc_info=True)
        return
    if not slugs:
        return

    showcase: set[str] = db.info.setdefault(_PENDING_SHOWCASE_KEYS, set())
    showcase.add(LOCATIONS_INDEX_CACHE_KEY)
    showcase.add(LAST_RESULTS_CACHE_KEY)

    keys: set[str] = db.info.setdefault(_PENDING_KEYS, set())
    for slug in slugs:
        keys.add(location_page_cache_key(slug))
        keys.add(location_events_cache_key(slug))
        keys.add(location_leaders_cache_key(slug))
    protocol_pairs = list(protocols)
    if protocol_pairs:
        from app.services.location_protocol_service import location_protocol_cache_key

        for slug in slugs:
            for platform_code, event_date in protocol_pairs:
                keys.add(location_protocol_cache_key(slug, platform_code, event_date))


@sa_event.listens_for(Session, "after_commit")
def _expire_marked_location_caches(session: Session) -> None:
    """Подрезать TTL помеченных витрин — уже после коммита.

    Никаких запросов к БД здесь: after_commit срабатывает вне транзакции, и
    новый SELECT открыл бы транзакцию, которую никто не закроет (ровно так
    выедается пул). Слаги посчитаны заранее, остаётся только Redis.
    """
    keys = session.info.pop(_PENDING_KEYS, None)
    showcase = session.info.pop(_PENDING_SHOWCASE_KEYS, None)
    if not keys and not showcase:
        return
    try:
        capped = _cap_ttl(keys or (), STALE_AFTER_WRITE_SECONDS)
        capped += _cap_ttl(showcase or (), STALE_AFTER_WRITE_SECONDS_SHOWCASE)
    except redis.RedisError:
        # Redis недоступен — витрины доживут до своего TTL, ронять синк незачем.
        logger.warning("Не удалось подрезать TTL витрин локаций", exc_info=True)
        return
    if capped:
        logger.info("Витрины локаций помечены устаревшими: ключей %s", capped)


@sa_event.listens_for(Session, "after_soft_rollback")
def _drop_marked_location_caches(session: Session, previous_transaction: object) -> None:
    if session.in_transaction():
        return
    session.info.pop(_PENDING_KEYS, None)
    session.info.pop(_PENDING_SHOWCASE_KEYS, None)
