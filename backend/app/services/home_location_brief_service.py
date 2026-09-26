"""Домашняя локация пользователя одной строкой {slug, name} — для ответа /auth/me.

Нужна навигации и поиску: «погода» без названия парка ведёт в погоду своей
локации, колонка «Локации» показывает «Моя: Мещерский».

/auth/me зовут на каждой загрузке страницы, поэтому здесь нельзя строить
детализацию по локациям, как это делают настройки (resolve_home_location →
build_user_unique_location_details). Только дешёвые шаги:

1. Выбранная вручную (users.home_location_key) — ищем её в каталоге локаций,
   который и так лежит в Redis (прогрев locations_warm).
2. Иначе — где у человека больше всего пробежек: один group by по его
   пробежкам и сопоставление локаций с узлами каталога по связкам (так же,
   как LocationCatalogIndex, — catalog_ids_for_locations). Это первая
   и третья ступени автоотбора из home_location_service (пробежки, при ничьей
   — где начал); волонтёрства как второй признак здесь не считаем — ради них
   пришлось бы строить ту самую детализацию. Расхождения возможны только при
   полной ничьей по пробежкам.

Каталог не прогрет (в Redis пусто) — отвечаем null и НЕ считаем его: поиск и
навигация переживут, а /auth/me тормозить не должен.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from sqlalchemy import func, tuple_
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.location_catalog_service import catalog_ids_for_locations

logger = logging.getLogger(__name__)

# Карта «узел каталога → {slug, name}» живёт в памяти процесса: разбирать
# JSON каталога (~300 строк) на каждый /auth/me незачем, а название и адрес
# локации меняются раз в месяцы.
_MAP_TTL_SECONDS = 600
# Каталога в Redis нет — пробуем снова скоро, а не через десять минут.
_EMPTY_TTL_SECONDS = 60

_memo: dict[str, Any] = {"at": 0.0, "ttl": 0.0, "map": {}}


def _identity_map() -> dict[str, dict[str, str]]:
    now = time.monotonic()
    if now - float(_memo["at"]) < float(_memo["ttl"]):
        return _memo["map"]  # type: ignore[no-any-return]
    from app.services.location_page_service import _read_locations_index_cache

    index = _read_locations_index_cache()
    mapping: dict[str, dict[str, str]] = {}
    if index:
        for entry in [*(index.get("items") or []), *(index.get("series") or [])]:  # type: ignore[misc]
            key = str(entry.get("identity_key") or "")
            slug = str(entry.get("slug") or "")
            name = str(entry.get("name") or "")
            if key and slug and name:
                mapping[key] = {"slug": slug, "name": name}
    _memo.update(at=now, ttl=_MAP_TTL_SECONDS if mapping else _EMPTY_TTL_SECONDS, map=mapping)
    return mapping


def reset_home_location_memo() -> None:
    """Для тестов: забыть карту каталога."""
    _memo.update(at=0.0, ttl=0.0, map={})


def _user_participant_ids(db: Session, user_id: UUID) -> list[UUID]:
    links = db.query(PlatformLink.participant_id, PlatformLink.platform_id, PlatformLink.external_user_id).filter(
        PlatformLink.user_id == user_id
    )
    ids: set[UUID] = set()
    by_identity: list[tuple[UUID, str]] = []
    for participant_id, platform_id, external_user_id in links:
        if participant_id is not None:
            ids.add(participant_id)
        elif external_user_id:
            by_identity.append((platform_id, external_user_id))
    if by_identity:
        ids.update(
            participant_id
            for (participant_id,) in db.query(Participant.id).filter(
                tuple_(Participant.platform_id, Participant.external_user_id).in_(by_identity)
            )
        )
    return list(ids)


def _identity_keys(db: Session, location_ids: list[UUID]) -> dict[UUID, str]:
    """location_id → ключ узла, как canonical_identity_key, но без загрузки всего каталога.

    Узел ищется ровно как в LocationCatalogIndex (catalog_ids_for_locations):
    по связке, затем по слагу в системе — как есть и нормализованному, со
    слагами эпохи parkrun. Не нашёлся — площадка сама себе узел «location:<id>».
    """
    rows = (
        db.query(Location.id, Platform.code, Location.external_key)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Location.id.in_(location_ids))
        .all()
    )
    catalog_ids = catalog_ids_for_locations(
        db, {location_id: (platform_code, external_key) for location_id, platform_code, external_key in rows}
    )
    return {
        location_id: f"catalog:{catalog_ids[location_id]}" if location_id in catalog_ids else f"location:{location_id}"
        for location_id in location_ids
    }


def _auto_identity(db: Session, user_id: UUID, known: dict[str, dict[str, str]]) -> str | None:
    participant_ids = _user_participant_ids(db, user_id)
    if not participant_ids:
        return None
    rows = (
        db.query(Event.location_id, func.count(RunResult.id), func.min(Event.event_date))
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id.in_(participant_ids), Event.is_test_event.is_(False))
        .group_by(Event.location_id)
        .all()
    )
    if not rows:
        return None
    keys = _identity_keys(db, [location_id for location_id, _count, _first in rows])
    totals: dict[str, tuple[int, Any]] = {}
    for location_id, count, first_date in rows:
        key = keys.get(location_id)
        if key is None or key not in known:
            continue
        runs, first = totals.get(key, (0, None))
        earliest = first_date if first is None or (first_date is not None and first_date < first) else first
        totals[key] = (runs + int(count), earliest)
    if not totals:
        return None
    # Больше пробежек — выше; при ничьей — где человек начал (раньше дата).
    return min(totals, key=lambda key: (-totals[key][0], totals[key][1].isoformat() if totals[key][1] else "9999"))


def home_location_brief(db: Session, user: User) -> dict[str, str] | None:
    """{slug, name} домашней локации или None. Сбой — тоже None: это подсказка, не данные."""
    try:
        known = _identity_map()
        if not known:
            return None
        if user.home_location_key and user.home_location_key in known:
            return dict(known[user.home_location_key])
        key = _auto_identity(db, user.id, known)
        return dict(known[key]) if key else None
    except Exception:  # noqa: BLE001 — подсказка не должна ломать /auth/me
        logger.exception("home location brief failed for user %s", user.id)
        return None
