"""Каталог parkrun из боевой базы — для импорта профиля, лечения и ссылок.

С 18.10.2026 каталог площадок живёт в таблицах parkrun_events /
parkrun_event_names / parkrun_countries и обновляется сверкой сайта
(services/parkrun_catalog_service.py). Снимки JSON в репо (event_slugs.py)
остаются запасным путём: пустая или недоступная таблица (стенд, тесты, база
до сида) — работаем по ним, как раньше.

Парсер профиля вызывается без сессии базы, поэтому сам слаг он берёт из
снимка, а импорт (у него сессия есть) переспрашивает каталог в базе по имени
площадки: новая площадка, открывшаяся после снимка, сразу получает слаг parkrun.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.parkrun.event_slugs import CatalogEvent, catalog_event, normalize_event_name, real_point

logger = logging.getLogger(__name__)


def _query(db: Session, sql: str, params: dict) -> list | None:
    """Запрос к каталогу без риска для транзакции вызывающего: ошибка — None."""
    try:
        with db.begin_nested():
            return db.execute(text(sql), params).all()
    except SQLAlchemyError:
        logger.warning("parkrun catalog: таблица недоступна, работаем по снимку", exc_info=True)
        return None


def official_slug_by_name(db: Session, name: str | None) -> str | None:
    """Слаг parkrun по названию площадки из профиля — если оно однозначно."""
    key = normalize_event_name(name or "")
    if not key:
        return None
    rows = _query(
        db,
        """
        select distinct e.slug, e.is_active
          from parkrun_event_names n join parkrun_events e on e.id = n.event_id
         where n.name_norm = :key
        """,
        {"key": key},
    )
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0][0]
    active = [slug for slug, is_active in rows if is_active]
    return active[0] if len(active) == 1 else None


def is_catalog_slug(db: Session, slug: str) -> bool:
    rows = _query(db, "select 1 from parkrun_events where slug = :slug", {"slug": slug})
    return bool(rows)


def resolve_catalog_event(db: Session | None, slug: str | None) -> CatalogEvent | None:
    """Площадка по слагу: сначала каталог в базе, иначе снимок в репо.

    Домен — только у действующей страны: у России и Франции его нет, и ссылку
    на такую площадку не строим.
    """
    if not slug:
        return None
    if db is not None:
        cache = _event_cache(db)
        if slug in cache:
            return cache[slug]
        rows = _query(db, _EVENT_SQL + " where e.slug = :slug", {"slug": slug})
        if rows:
            cache[slug] = _event_from_row(rows[0])
            return cache[slug]
        if rows is not None and catalog_has_rows(db):
            cache[slug] = None
            return None
    return catalog_event(slug)


_EVENT_SQL = """
    select e.slug, e.country_code, e.latitude, e.longitude, e.is_active,
           case when c.is_active then c.domain end
      from parkrun_events e left join parkrun_countries c on c.code = e.country_code
"""


def _event_from_row(row) -> CatalogEvent | None:
    event_slug, code, latitude, longitude, is_active, domain = row
    if code is None:
        return None
    point = real_point(latitude, longitude)
    return CatalogEvent(
        slug=event_slug,
        country_code=int(code),
        latitude=point[0] if point else None,
        longitude=point[1] if point else None,
        country_domain=domain,
        is_active=bool(is_active),
    )


def _event_cache(db: Session) -> dict[str, CatalogEvent | None]:
    """Площадки, уже прочитанные этой сессией. Каталог меняется раз в сутки —
    в пределах импорта или прогона лечения он не устаревает."""
    return db.info.setdefault("parkrun_catalog_events", {})


def prefetch_catalog_events(db: Session, slugs) -> None:
    """Прочитать площадки пачкой — чтобы resolve_catalog_event не ходил в базу на каждую."""
    cache = _event_cache(db)
    wanted = sorted({slug for slug in slugs if slug and slug not in cache})
    if not wanted:
        return
    rows = _query(db, _EVENT_SQL + " where e.slug = any(:slugs)", {"slugs": wanted})
    if rows is None:
        return
    for row in rows:
        cache[row[0]] = _event_from_row(row)
    if catalog_has_rows(db):
        for slug in wanted:
            cache.setdefault(slug, None)


def catalog_has_rows(db: Session) -> bool:
    """Засеян ли каталог в базе. Пустой — значит, работаем по снимку."""
    cached = db.info.get("parkrun_catalog_has_rows")
    if cached is not None:
        return bool(cached)
    rows = _query(db, "select exists(select 1 from parkrun_events)", {})
    value = bool(rows and rows[0][0])
    db.info["parkrun_catalog_has_rows"] = value
    return value


def active_parkrun_slugs(db: Session) -> set[str] | None:
    """Слаги действующих площадок каталога. None — каталог в базе не засеян."""
    cached = db.info.get("parkrun_active_slugs", ...)
    if cached is not ...:
        return cached
    rows = _query(db, "select slug from parkrun_events where is_active", {})
    value = {row[0] for row in rows} if rows else None
    db.info["parkrun_active_slugs"] = value
    return value
