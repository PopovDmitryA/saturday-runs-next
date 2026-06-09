#!/usr/bin/env python3
"""Backfill location city, region, country from legacy five_verst_stats."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.migration.helpers import (
    parse_float,
    s95_country_from_url,
    slug_from_5verst_url,
    slug_from_s95_url,
)
from app.migration.legacy_db import legacy_connection, legacy_rows
from app.models import Location, Platform

BELARUS_SLUGS = frozenset({"brest", "gomel", "grodno"})

# (platform_code, external_key) -> canonical city
CITY_OVERRIDES: dict[tuple[str, str], str] = {
    ("five_verst", "mariupolprimorskiy"): "Мариуполь",
    ("s95", "troitsk"): "Троицк",
}


@dataclass
class LegacyLocationFields:
    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class BackfillStats:
    five_verst_matched: int = 0
    s95_matched: int = 0
    updated: int = 0
    country_fixed: int = 0
    city_overrides: int = 0
    skipped_no_legacy: int = 0
    errors: list[str] = field(default_factory=list)


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_five_verst_legacy(conn) -> dict[str, LegacyLocationFields]:
    result: dict[str, LegacyLocationFields] = {}
    for row in legacy_rows(
        conn,
        """
        SELECT link_point, city, region, latitude, longitude
        FROM general_location
        WHERE link_point IS NOT NULL
        """,
    ):
        slug = slug_from_5verst_url(str(row["link_point"]))
        if not slug:
            continue
        result[slug] = LegacyLocationFields(
            city=_clean_text(row.get("city")),
            region=_clean_text(row.get("region")),
            country="Россия",
            latitude=parse_float(row.get("latitude")),
            longitude=parse_float(row.get("longitude")),
        )
    return result


def load_s95_legacy(conn) -> dict[str, LegacyLocationFields]:
    result: dict[str, LegacyLocationFields] = {}
    for row in legacy_rows(
        conn,
        """
        SELECT link_point, city, region, latitude, longitude
        FROM s95_location
        WHERE link_point IS NOT NULL
        """,
    ):
        source_url = str(row["link_point"]).strip()
        slug = slug_from_s95_url(source_url)
        if not slug:
            continue
        result[slug] = LegacyLocationFields(
            city=_clean_text(row.get("city")),
            region=_clean_text(row.get("region")),
            country=s95_country_from_url(source_url),
            latitude=parse_float(row.get("latitude")),
            longitude=parse_float(row.get("longitude")),
        )
    return result


def _apply_fields(
    location: Location,
    legacy: LegacyLocationFields,
    *,
    dry_run: bool,
) -> bool:
    changed = False

    def _set_if_missing(attr: str, value: str | None) -> None:
        nonlocal changed
        if not value:
            return
        current = getattr(location, attr)
        if current is not None and str(current).strip():
            return
        if not dry_run:
            setattr(location, attr, value)
        changed = True

    _set_if_missing("city", legacy.city)
    _set_if_missing("region", legacy.region)
    if legacy.country and location.country != legacy.country:
        if not dry_run:
            location.country = legacy.country
        changed = True
    if legacy.latitude is not None and location.latitude is None:
        if not dry_run:
            location.latitude = legacy.latitude
        changed = True
    if legacy.longitude is not None and location.longitude is None:
        if not dry_run:
            location.longitude = legacy.longitude
        changed = True
    return changed


def _fix_special_countries(location: Location, *, dry_run: bool) -> bool:
    changed = False
    source = (location.source_url or "").lower()
    target_country: str | None = None

    if location.external_key in BELARUS_SLUGS or "s95.by/" in source:
        target_country = "Беларусь"
    elif "s95.rs/" in source or location.external_key in {"novisad", "belgrade"}:
        target_country = "Сербия"

    if target_country and location.country != target_country:
        if not dry_run:
            location.country = target_country
        changed = True
    return changed


def backfill_platform(
    db: Session,
    platform: Platform,
    legacy_by_slug: dict[str, LegacyLocationFields],
    stats: BackfillStats,
    *,
    dry_run: bool,
) -> None:
    locations = db.query(Location).filter(Location.platform_id == platform.id).all()
    for location in locations:
        legacy = legacy_by_slug.get(location.external_key)
        if legacy is None:
            stats.skipped_no_legacy += 1
            if _fix_special_countries(location, dry_run=dry_run):
                stats.country_fixed += 1
                stats.updated += 1
            continue

        if platform.code == "five_verst":
            stats.five_verst_matched += 1
        elif platform.code == "s95":
            stats.s95_matched += 1

        changed = _apply_fields(location, legacy, dry_run=dry_run)
        if _fix_special_countries(location, dry_run=dry_run):
            stats.country_fixed += 1
            changed = True
        if changed:
            stats.updated += 1


def apply_city_overrides(db: Session, stats: BackfillStats, *, dry_run: bool) -> None:
    platforms = {p.code: p for p in db.query(Platform).all()}
    for (platform_code, external_key), city in CITY_OVERRIDES.items():
        platform = platforms.get(platform_code)
        if platform is None:
            stats.errors.append(f"platform missing for override: {platform_code}")
            continue
        location = (
            db.query(Location)
            .filter(
                Location.platform_id == platform.id,
                Location.external_key == external_key,
            )
            .one_or_none()
        )
        if location is None:
            stats.errors.append(f"location missing for override: {platform_code}/{external_key}")
            continue
        if location.city == city:
            continue
        if not dry_run:
            location.city = city
        stats.city_overrides += 1
        stats.updated += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    Session = get_session_factory()
    stats = BackfillStats()

    with legacy_connection() as conn:
        five_verst_legacy = load_five_verst_legacy(conn)
        s95_legacy = load_s95_legacy(conn)

        with Session() as db:
            for code, legacy_map in (("five_verst", five_verst_legacy), ("s95", s95_legacy)):
                platform = db.query(Platform).filter(Platform.code == code).one_or_none()
                if platform is None:
                    stats.errors.append(f"platform missing: {code}")
                    continue
                backfill_platform(db, platform, legacy_map, stats, dry_run=args.dry_run)

            apply_city_overrides(db, stats, dry_run=args.dry_run)

            if args.dry_run:
                db.rollback()
            else:
                db.commit()

    print(
        f"five_verst matched={stats.five_verst_matched} "
        f"s95 matched={stats.s95_matched} updated={stats.updated} "
        f"country_fixed={stats.country_fixed} city_overrides={stats.city_overrides} "
        f"skipped_no_legacy={stats.skipped_no_legacy}",
        flush=True,
    )
    if stats.errors:
        for err in stats.errors:
            print(f"error: {err}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
