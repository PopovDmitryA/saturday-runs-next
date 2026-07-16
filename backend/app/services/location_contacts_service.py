"""Контакты локаций: список для админки, правка ссылок/настроек и импорт легаси."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Location, LocationAnnounceSettings, LocationContact, Platform

# parkrun не анонсируем — рассылки идут только по этим платформам.
CONTACT_PLATFORM_CODES = ("five_verst", "s95", "runpark")

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


def list_location_contacts(
    db: Session,
    *,
    query: str | None = None,
    only_missing: bool = False,
    only_do_not_disturb: bool = False,
) -> list[dict[str, Any]]:
    stmt = (
        select(Location, Platform, LocationAnnounceSettings)
        .join(Platform, Platform.id == Location.platform_id)
        .outerjoin(LocationAnnounceSettings, LocationAnnounceSettings.location_id == Location.id)
        .where(Platform.code.in_(CONTACT_PLATFORM_CODES))
        .order_by(Location.name, Platform.code)
    )
    if query:
        needle = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(Location.name).like(needle), func.lower(Location.city).like(needle))
        )
    if only_do_not_disturb:
        stmt = stmt.where(LocationAnnounceSettings.do_not_disturb.is_(True))

    rows = db.execute(stmt).all()
    location_ids = [location.id for location, _platform, _settings in rows]
    contacts_by_location: dict[UUID, list[LocationContact]] = {}
    if location_ids:
        for contact in (
            db.query(LocationContact)
            .filter(LocationContact.location_id.in_(location_ids))
            .order_by(LocationContact.created_at)
            .all()
        ):
            contacts_by_location.setdefault(contact.location_id, []).append(contact)

    items: list[dict[str, Any]] = []
    for location, platform, settings in rows:
        contacts = contacts_by_location.get(location.id, [])
        if only_missing and contacts:
            continue
        items.append(
            {
                "location_id": location.id,
                "location_name": location.name,
                "city": location.city,
                "country": location.country,
                "platform_code": platform.code,
                "platform_name": platform.name,
                "is_cancelled": location.is_cancelled,
                "is_paused": location.is_paused,
                "contacts": [
                    {"id": c.id, "telegram_url": c.telegram_url, "label": c.label} for c in contacts
                ],
                "do_not_disturb": bool(settings.do_not_disturb) if settings else False,
                "comment": settings.comment if settings else None,
                "updated_at": settings.updated_at if settings else None,
            }
        )
    return items


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
