"""Доступ к кабинету организатора локации.

Доступ к кабинету есть у двух кругов людей:
- автодоступ: по данным протоколов человек хоть раз волонтёрил на локации в
  роли организатора (канонический ключ run_director из volunteer_role_taxonomy:
  «Организатор» у 5 вёрст, «Руководитель» у RunPark, «Run Director» у parkrun,
  «Директор» у С95);
- ручной грант из админки (таблица location_organizer_access).

Локация везде — каноническая идентичность каталога (identity key), поэтому
организатор 5 вёрст видит и S95-половину той же физической точки. Роли
канонизируются в Python (canonical_volunteer_role), а не в SQL: сырые ярлыки
в БД несут счётчики вида «Run Director (12×)» и разные написания по системам.

Список доступных локаций кэшируется в Redis на час: он нужен и пункту меню
(is_organizer в /auth/me на каждый заход), и самому разделу. Ручной грант
инвалидирует кэш сразу; автодоступ меняется только субботним синком, часа TTL
достаточно.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import redis
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import (
    Event,
    Location,
    LocationOrganizerAccess,
    Participant,
    Platform,
    PlatformLink,
    User,
    VolunteerResult,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_page_service import _read_json_cache, _write_json_cache
from app.volunteer_role_taxonomy import canonical_volunteer_role

ORGANIZER_ROLE_KEY = "run_director"

ORGANIZER_LOCATIONS_CACHE_TTL_SECONDS = 60 * 60


def organizer_locations_cache_key(user_id: UUID) -> str:
    return f"organizer:locations:v1:{user_id}"


def invalidate_organizer_locations_cache(user_id: UUID) -> None:
    try:
        get_redis_client().delete(organizer_locations_cache_key(user_id))
    except redis.RedisError:
        pass


def derive_organizer_identity_keys(
    db: Session, user_id: UUID, catalog_index: LocationCatalogIndex | None = None
) -> set[str]:
    """Идентичности локаций, где пользователь волонтёрил организатором.

    users → platform_links → participants → volunteer_results → events →
    locations; надёжный ключ между participants и platform_links —
    (platform_id, external_user_id), как в location_page_service.
    """
    rows = (
        db.query(VolunteerResult.role, Location, Platform.code)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .join(Participant, VolunteerResult.participant_id == Participant.id)
        .join(
            PlatformLink,
            and_(
                PlatformLink.platform_id == Participant.platform_id,
                PlatformLink.external_user_id == Participant.external_user_id,
            ),
        )
        .filter(
            PlatformLink.user_id == user_id,
            VolunteerResult.role.isnot(None),
            Event.is_test_event.is_(False),
        )
        .all()
    )
    # Канонизируем роли ДО построения каталога: LocationCatalogIndex — полный
    # скан каталога, а вызов идёт на каждый /auth/me. У большинства волонтёров
    # организаторских ролей нет, и до каталога дело не доходит.
    organizer_locations = [
        (location, platform_code)
        for role, location, platform_code in rows
        if (canonical := canonical_volunteer_role(role)) is not None
        and canonical.key == ORGANIZER_ROLE_KEY
    ]
    if not organizer_locations:
        return set()
    if catalog_index is None:
        catalog_index = LocationCatalogIndex(db)
    return {
        catalog_index.canonical_identity_key(location, platform_code)
        for location, platform_code in organizer_locations
    }


def manual_grant_identity_keys(db: Session, user_id: UUID) -> set[str]:
    rows = (
        db.query(LocationOrganizerAccess.location_key)
        .filter(LocationOrganizerAccess.user_id == user_id)
        .all()
    )
    return {key for (key,) in rows}


def build_organizer_locations(
    db: Session, user: User, *, use_cache: bool = True, refresh: bool = False
) -> dict[str, Any]:
    """Доступные пользователю локации кабинета организатора (с Redis-кэшем).

    Элементы — строки каталога локаций (slug, name, platform_codes) с пометкой
    источника доступа: volunteering / manual / both.
    """
    cache_key = organizer_locations_cache_key(user.id)
    if use_cache and not refresh:
        cached = _read_json_cache(cache_key)
        if cached is not None:
            return cached

    payload = _compute_organizer_locations(db, user)

    if use_cache:
        _write_json_cache(cache_key, payload, ORGANIZER_LOCATIONS_CACHE_TTL_SECONDS)
    return payload


def build_admin_organizer_locations(db: Session) -> dict[str, Any]:
    """Список локаций кабинета для админа: весь каталог, source='admin'.

    Просьба Дмитрия 24.08.2026: админ должен видеть /organizer «как будто он
    организатор везде», а не пустую страницу с подсказкой про прямые ссылки.
    Кэш не нужен — build_locations_index уже кэширован.
    """

    from app.services.location_page_service import build_locations_index

    index = build_locations_index(db)
    items = [
        {
            "location_key": entry.get("identity_key"),
            "slug": entry.get("slug"),
            "name": entry.get("name"),
            "city": entry.get("city"),
            "platform_codes": entry.get("platform_codes"),
            "is_paused": entry.get("is_paused"),
            "access_source": "admin",
        }
        for entry in index.get("items", [])
    ]
    items.sort(key=lambda item: (item.get("name") or ""))
    return {"items": items, "total": len(items)}


def _compute_organizer_locations(db: Session, user: User) -> dict[str, Any]:
    from app.services.location_page_service import build_locations_index

    derived = derive_organizer_identity_keys(db, user.id)
    manual = manual_grant_identity_keys(db, user.id)
    accessible = derived | manual
    if not accessible:
        return {"items": [], "total": 0}

    index = build_locations_index(db)
    items: list[dict[str, Any]] = []
    for entry in index.get("items", []):
        identity_key = entry.get("identity_key")
        if identity_key not in accessible:
            continue
        if identity_key in derived and identity_key in manual:
            source = "both"
        elif identity_key in derived:
            source = "volunteering"
        else:
            source = "manual"
        items.append(
            {
                "location_key": identity_key,
                "slug": entry.get("slug"),
                "name": entry.get("name"),
                "city": entry.get("city"),
                "platform_codes": entry.get("platform_codes"),
                "is_paused": entry.get("is_paused"),
                "access_source": source,
            }
        )
    return {"items": items, "total": len(items)}


def user_is_organizer(db: Session, user: User) -> bool:
    """Есть ли у пользователя хоть одна локация в кабинете организатора.

    Ходит через кэшированный build_organizer_locations — вызывается на каждый
    /auth/me, без кэша это был бы тяжёлый запрос на каждый заход на сайт.
    """
    payload = build_organizer_locations(db, user)
    return bool(payload.get("total"))


def has_organizer_access(db: Session, user: User, identity_key: str) -> bool:
    payload = build_organizer_locations(db, user)
    return any(item.get("location_key") == identity_key for item in payload.get("items", []))
