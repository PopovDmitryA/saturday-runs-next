"""Контакты локаций: список для админки, правка ссылок/настроек и импорт легаси."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Location,
    LocationAnnounceSettings,
    LocationCatalog,
    LocationCatalogLink,
    LocationContact,
    Platform,
)
from app.services.location_catalog_service import normalize_platform_code

# parkrun не анонсируем — рассылки идут только по этим платформам.
CONTACT_PLATFORM_CODES = ("five_verst", "s95", "runpark")

# Порядок «якорной» платформы в группе: локация, на которую вешаются общие для
# физической точки чат и настройки анонса. active_platform каталога главнее
# всего; при его отсутствии — вот этот порядок.
_ANCHOR_PLATFORM_ORDER = {"five_verst": 1, "s95": 2, "runpark": 3}

SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "location_contacts_seed.json"


class LocationContactError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_point_name(value: str) -> str:
    """Ключ сопоставления имён локаций между легаси и текущей БД.

    Легаси-имена писались руками, поэтому расходятся с текущими по регистру,
    ё/е и пробелам: «Мещерский » и «мещёрский» — одна и та же точка.
    """
    cleaned = unicodedata.normalize("NFKC", value).strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", cleaned)


def _telegram_url_valid(value: str) -> bool:
    return bool(re.match(r"^https://t\.me/[\w+/@-]+$", value))


def _get_location_for_contacts(db: Session, location_id: UUID) -> Location:
    location = (
        db.query(Location)
        .options(joinedload(Location.platform))
        .filter(Location.id == location_id)
        .one_or_none()
    )
    if location is None:
        raise LocationContactError("Локация не найдена", status_code=404)
    if location.platform.code not in CONTACT_PLATFORM_CODES:
        raise LocationContactError("Для этой платформы контакты не ведутся")
    return location


def _catalog_by_location(db: Session, location_ids: list[UUID]) -> dict[UUID, LocationCatalog]:
    """location_id → каталог физической точки (для локаций, связанных в справочнике)."""
    if not location_ids:
        return {}
    rows = (
        db.query(LocationCatalogLink.location_id, LocationCatalog)
        .join(LocationCatalog, LocationCatalog.id == LocationCatalogLink.catalog_id)
        .filter(LocationCatalogLink.location_id.in_(location_ids))
        .all()
    )
    return {location_id: catalog for location_id, catalog in rows if location_id is not None}


def _anchor_sort_key(
    platform_code: str, active_platform: str | None, location_name: str
) -> tuple[int, str]:
    if active_platform and platform_code == active_platform:
        rank = 0
    else:
        rank = _ANCHOR_PLATFORM_ORDER.get(platform_code, 9)
    return (rank, location_name)


def list_location_contacts(
    db: Session,
    *,
    query: str | None = None,
    only_missing: bool = False,
    only_do_not_disturb: bool = False,
) -> list[dict[str, Any]]:
    """Контакты, сгруппированные по физической точке.

    Связанные в справочнике локации разных платформ (5 вёрст + RunPark + S95 —
    одна и та же площадка) сводятся в одну строку: чат у точки общий, поэтому
    показываем и ведём его на «якорной» локации группы, а не плодим дубли по
    строке на каждую платформу. Несвязанные локации остаются сами по себе.
    """
    rows = db.execute(
        select(Location, Platform, LocationAnnounceSettings)
        .join(Platform, Platform.id == Location.platform_id)
        .outerjoin(LocationAnnounceSettings, LocationAnnounceSettings.location_id == Location.id)
        .where(Platform.code.in_(CONTACT_PLATFORM_CODES))
    ).all()

    all_location_ids = [location.id for location, _platform, _settings in rows]
    catalog_by_location = _catalog_by_location(db, all_location_ids)
    contacts_by_location: dict[UUID, list[LocationContact]] = {}
    if all_location_ids:
        for contact in (
            db.query(LocationContact)
            .filter(LocationContact.location_id.in_(all_location_ids))
            .order_by(LocationContact.created_at)
            .all()
        ):
            contacts_by_location.setdefault(contact.location_id, []).append(contact)

    groups: dict[str, list[tuple[Location, Platform, LocationAnnounceSettings | None]]] = {}
    for location, platform, settings in rows:
        catalog = catalog_by_location.get(location.id)
        group_key = f"catalog:{catalog.id}" if catalog is not None else f"location:{location.id}"
        groups.setdefault(group_key, []).append((location, platform, settings))

    needle = query.strip().lower() if query else None

    items: list[dict[str, Any]] = []
    for group_key, members in groups.items():
        catalog = catalog_by_location.get(members[0][0].id)
        active_platform = normalize_platform_code(catalog.active_platform) if catalog else None
        members = sorted(
            members,
            key=lambda m: _anchor_sort_key(m[1].code, active_platform, m[0].name),
        )
        anchor_location, _anchor_platform, anchor_settings = members[0]
        display_name = catalog.canonical_name if catalog else anchor_location.name

        if needle:
            haystacks = [display_name.lower()]
            for location, _platform, _settings in members:
                haystacks.append(location.name.lower())
                if location.city:
                    haystacks.append(location.city.lower())
            if not any(needle in value for value in haystacks):
                continue

        contacts: list[LocationContact] = []
        for location, _platform, _settings in members:
            contacts.extend(contacts_by_location.get(location.id, []))
        if only_missing and contacts:
            continue

        do_not_disturb = bool(anchor_settings.do_not_disturb) if anchor_settings else False
        if only_do_not_disturb and not do_not_disturb:
            continue

        items.append(
            {
                "group_key": group_key,
                "anchor_location_id": anchor_location.id,
                "display_name": display_name,
                "city": anchor_location.city,
                "country": anchor_location.country,
                "platforms": [
                    {
                        "location_id": location.id,
                        "location_name": location.name,
                        "platform_code": platform.code,
                        "platform_name": platform.name,
                        "is_cancelled": location.is_cancelled,
                        "is_paused": location.is_paused,
                    }
                    for location, platform, _settings in members
                ],
                "contacts": [
                    {"id": c.id, "telegram_url": c.telegram_url, "label": c.label} for c in contacts
                ],
                "do_not_disturb": do_not_disturb,
                "comment": anchor_settings.comment if anchor_settings else None,
                "updated_at": anchor_settings.updated_at if anchor_settings else None,
            }
        )

    items.sort(key=lambda item: item["display_name"].lower())
    return items


def resolve_group_announce_targets(
    db: Session, location_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    """Для рассылки: чат и настройки анонса физической точки для каждой локации.

    Рекорд считается на конкретной локации-платформе, но чат у точки общий (все
    125 легаси-контактов лежат на 5 вёрст). Поэтому для рекорда на RunPark/S95
    берём контакты и флаг «не беспокоить» со всей группы через справочник —
    иначе анонс уходил бы без чата или мимо выставленного «не беспокоить».
    Локации вне справочника обслуживаются сами по себе.
    """
    location_ids = list(dict.fromkeys(location_ids))
    if not location_ids:
        return {}

    catalog_by_location = _catalog_by_location(db, location_ids)
    catalog_ids = {catalog.id for catalog in catalog_by_location.values()}

    # Все локации-члены затронутых групп (включая sibling'ов, которых нет во
    # входном списке): (catalog_id, Location, Platform).
    members_by_catalog: dict[UUID, list[tuple[Location, Platform]]] = {}
    if catalog_ids:
        for catalog_id, location, platform in (
            db.query(LocationCatalogLink.catalog_id, Location, Platform)
            .join(Location, Location.id == LocationCatalogLink.location_id)
            .join(Platform, Platform.id == Location.platform_id)
            .filter(
                LocationCatalogLink.catalog_id.in_(catalog_ids),
                Platform.code.in_(CONTACT_PLATFORM_CODES),
            )
            .all()
        ):
            members_by_catalog.setdefault(catalog_id, []).append((location, platform))

    def group_member_ids(location_id: UUID) -> list[UUID]:
        catalog = catalog_by_location.get(location_id)
        if catalog is None:
            return [location_id]
        ids = [location.id for location, _platform in members_by_catalog.get(catalog.id, [])]
        return ids or [location_id]

    def anchor_of(location_id: UUID) -> UUID:
        catalog = catalog_by_location.get(location_id)
        if catalog is None:
            return location_id
        members = members_by_catalog.get(catalog.id, [])
        if not members:
            return location_id
        active_platform = normalize_platform_code(catalog.active_platform)
        anchor = min(members, key=lambda m: _anchor_sort_key(m[1].code, active_platform, m[0].name))
        return anchor[0].id

    involved_ids: set[UUID] = set()
    for location_id in location_ids:
        involved_ids.update(group_member_ids(location_id))
        involved_ids.add(anchor_of(location_id))

    contacts_by_location: dict[UUID, list[dict[str, Any]]] = {}
    for contact in (
        db.query(LocationContact)
        .filter(LocationContact.location_id.in_(involved_ids))
        .order_by(LocationContact.created_at)
        .all()
    ):
        contacts_by_location.setdefault(contact.location_id, []).append(
            {"id": contact.id, "telegram_url": contact.telegram_url, "label": contact.label}
        )

    settings_by_location = {
        settings.location_id: settings
        for settings in db.query(LocationAnnounceSettings).filter(
            LocationAnnounceSettings.location_id.in_(involved_ids)
        )
    }

    result: dict[UUID, dict[str, Any]] = {}
    for location_id in location_ids:
        contacts: list[dict[str, Any]] = []
        for member_id in group_member_ids(location_id):
            contacts.extend(contacts_by_location.get(member_id, []))
        anchor_settings = settings_by_location.get(anchor_of(location_id))
        result[location_id] = {
            "telegram_contacts": contacts,
            "do_not_disturb": bool(anchor_settings.do_not_disturb) if anchor_settings else None,
            "comment": anchor_settings.comment if anchor_settings else None,
        }
    return result


def update_location_announce_settings(
    db: Session, location_id: UUID, *, do_not_disturb: bool, comment: str | None
) -> dict[str, Any]:
    location = _get_location_for_contacts(db, location_id)
    settings = (
        db.query(LocationAnnounceSettings)
        .filter(LocationAnnounceSettings.location_id == location_id)
        .one_or_none()
    )
    if settings is None:
        settings = LocationAnnounceSettings(location_id=location_id)
        db.add(settings)
    settings.do_not_disturb = do_not_disturb
    settings.comment = (comment or "").strip() or None
    db.commit()
    db.refresh(settings)
    return {
        "location_id": location.id,
        "do_not_disturb": settings.do_not_disturb,
        "comment": settings.comment,
        "updated_at": settings.updated_at,
    }


def create_location_contact_link(
    db: Session, location_id: UUID, *, telegram_url: str, label: str | None
) -> dict[str, Any]:
    _get_location_for_contacts(db, location_id)
    url = telegram_url.strip()
    if not _telegram_url_valid(url):
        raise LocationContactError("Ссылка должна быть вида https://t.me/...")
    contact = LocationContact(location_id=location_id, telegram_url=url, label=(label or "").strip() or None)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return {"id": contact.id, "telegram_url": contact.telegram_url, "label": contact.label}


def update_location_contact_link(
    db: Session, contact_id: UUID, *, telegram_url: str, label: str | None
) -> dict[str, Any]:
    contact = db.query(LocationContact).filter(LocationContact.id == contact_id).one_or_none()
    if contact is None:
        raise LocationContactError("Ссылка не найдена", status_code=404)
    url = telegram_url.strip()
    if not _telegram_url_valid(url):
        raise LocationContactError("Ссылка должна быть вида https://t.me/...")
    contact.telegram_url = url
    contact.label = (label or "").strip() or None
    db.commit()
    db.refresh(contact)
    return {"id": contact.id, "telegram_url": contact.telegram_url, "label": contact.label}


def delete_location_contact_link(db: Session, contact_id: UUID) -> None:
    contact = db.query(LocationContact).filter(LocationContact.id == contact_id).one_or_none()
    if contact is None:
        raise LocationContactError("Ссылка не найдена", status_code=404)
    db.delete(contact)
    db.commit()


def import_legacy_contacts(
    db: Session, *, seed_path: Path | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Влить легаси-контакты 5 вёрст в location_contacts / location_announce_settings.

    Идемпотентен: ссылку добавляет только если у локации ещё нет контакта с
    таким же URL, do_not_disturb выставляет только в True (никогда не сбрасывает
    ручной False обратно) — повторный прогон не отменяет правки из админки.
    """
    path = seed_path or SEED_PATH
    seed_items = json.loads(path.read_text(encoding="utf-8"))["items"]
    seed_by_name = {normalize_point_name(item["name_point"]): item for item in seed_items}

    five_verst_id = db.execute(
        select(Platform.id).where(Platform.code == "five_verst")
    ).scalar_one_or_none()

    locations = (
        db.execute(
            select(Location)
            .options(joinedload(Location.platform))
            .join(Platform, Platform.id == Location.platform_id)
            .where(Platform.code.in_(CONTACT_PLATFORM_CODES))
        )
        .scalars()
        .all()
    )
    existing_contacts: dict[UUID, set[str]] = {}
    for contact in db.query(LocationContact).all():
        existing_contacts.setdefault(contact.location_id, set()).add(contact.telegram_url)
    existing_settings = {
        settings.location_id: settings for settings in db.query(LocationAnnounceSettings).all()
    }

    links_created = 0
    settings_touched = 0
    matched_names: set[str] = set()
    for location in locations:
        if location.platform_id != five_verst_id:
            continue
        key = normalize_point_name(location.name)
        seed = seed_by_name.get(key)
        if seed is None:
            continue
        matched_names.add(key)

        if seed["telegram_url"] and seed["telegram_url"] not in existing_contacts.get(location.id, set()):
            db.add(LocationContact(location_id=location.id, telegram_url=seed["telegram_url"]))
            existing_contacts.setdefault(location.id, set()).add(seed["telegram_url"])
            links_created += 1

        if seed["do_not_disturb"]:
            settings = existing_settings.get(location.id)
            if settings is None:
                db.add(LocationAnnounceSettings(location_id=location.id, do_not_disturb=True))
                settings_touched += 1
            elif not settings.do_not_disturb:
                settings.do_not_disturb = True
                settings_touched += 1

    # Несопоставленные легаси-имена — это, как правило, точки, названные по
    # городу и с тех пор переименованные или разделившиеся на две («Череповец»
    # → «Соборная набережная» + «Макаринская роща»). Автоматически такое
    # разносить нельзя, поэтому показываем кандидатов по городу для ручной правки.
    unmatched: list[dict[str, Any]] = []
    for key in sorted(set(seed_by_name) - matched_names):
        seed = seed_by_name[key]
        candidates = [
            f"{loc.name} ({loc.platform.code})"
            for loc in locations
            if loc.city and normalize_point_name(loc.city) == key
        ]
        unmatched.append(
            {
                "name_point": seed["name_point"],
                "telegram_url": seed["telegram_url"],
                "candidates": candidates,
            }
        )

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {
        "locations_total": len(locations),
        "contact_links_created": links_created,
        "announce_settings_touched": settings_touched,
        "legacy_rows": len(seed_items),
        "legacy_unmatched": unmatched,
    }
