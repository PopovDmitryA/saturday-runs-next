#!/usr/bin/env python3
"""Import 5verst / S95 location CSV dumps into the locations table."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent

if (REPO_ROOT / "data").is_dir():
    DATA_ROOT = REPO_ROOT
elif Path("/data").is_dir():
    DATA_ROOT = Path("/")
else:
    DATA_ROOT = REPO_ROOT

DEFAULT_S95_CSV = DATA_ROOT / "data" / "locations" / "s95_locations.csv"
DEFAULT_FIVE_VERST_CSV = DATA_ROOT / "data" / "locations" / "five_verst_locations.csv"

S95_URL_RE = re.compile(r"^https?://(?:www\.)?s95\.(?:ru|by|rs)/events/(?P<slug>[^/?#]+)", re.I)
FIVE_VERST_URL_RE = re.compile(r"^https?://(?:www\.)?5verst\.ru/(?P<slug>[^/?#]+)", re.I)


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes", "да"}


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).strip().strip('"').replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def slug_from_url(url: str, *, platform_code: str) -> str | None:
    trimmed = (url or "").strip()
    if not trimmed:
        return None
    if platform_code == "s95":
        match = S95_URL_RE.match(trimmed)
        return match.group("slug") if match else None
    match = FIVE_VERST_URL_RE.match(trimmed)
    return match.group("slug") if match else None


def infer_country(region: str | None) -> str | None:
    if not region:
        return None
    text = region.strip()
    if not text:
        return None
    if "Серб" in text or "Воеводина" in text:
        return None
    return "Россия"


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def import_platform_csv(
    db,
    *,
    platform_code: str,
    csv_path: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    from app.platform_adapters.canonical import CanonicalLocation
    from app.sync import upsert

    platform = upsert.get_platform(db, platform_code)
    rows = load_csv_rows(csv_path)
    stats = {"rows": 0, "imported": 0, "paused": 0, "skipped_no_coords": 0, "skipped_no_slug": 0}

    for row in rows:
        stats["rows"] += 1
        is_paused = parse_bool(row.get("is_pause"))

        link = (row.get("link_point") or "").strip()
        external_key = slug_from_url(link, platform_code=platform_code)
        if not external_key:
            stats["skipped_no_slug"] += 1
            continue

        latitude = parse_float(row.get("latitude"))
        longitude = parse_float(row.get("longitude"))
        if latitude is None or longitude is None:
            stats["skipped_no_coords"] += 1
            continue

        if is_paused:
            stats["paused"] += 1

        if platform_code == "s95":
            name = (row.get("full_name_point") or row.get("name_point") or external_key).strip()
        else:
            name = (row.get("name_point") or external_key).strip()

        city = (row.get("city") or "").strip() or None
        region = (row.get("region") or "").strip() or None
        canonical = CanonicalLocation(
            external_key=external_key,
            name=name,
            country=infer_country(region),
            city=city,
            region=region,
            latitude=latitude,
            longitude=longitude,
            source_url=link,
        )
        if dry_run:
            stats["imported"] += 1
            continue

        location, _changed = upsert.upsert_location(db, platform, canonical)
        location.is_paused = is_paused
        location.is_official_map = True
        _link_catalog_location(db, platform, location)
        stats["imported"] += 1

    return stats


def _link_catalog_location(db, platform, location) -> None:
    from app.models import LocationCatalogLink

    link = (
        db.query(LocationCatalogLink)
        .filter(
            LocationCatalogLink.platform_id == platform.id,
            LocationCatalogLink.external_key == location.external_key,
        )
        .one_or_none()
    )
    if link is not None and link.location_id != location.id:
        link.location_id = location.id


def import_to_db(
    *,
    s95_csv: Path,
    five_verst_csv: Path,
    dry_run: bool = False,
) -> dict[str, dict[str, int]]:
    backend_path = str(BACKEND_ROOT)
    if backend_path not in sys.path and (BACKEND_ROOT / "app").is_dir():
        sys.path.insert(0, backend_path)

    from app.db.session import get_session_factory

    db = get_session_factory()()
    try:
        result = {
            "s95": import_platform_csv(db, platform_code="s95", csv_path=s95_csv, dry_run=dry_run),
            "five_verst": import_platform_csv(
                db, platform_code="five_verst", csv_path=five_verst_csv, dry_run=dry_run
            ),
        }
        if not dry_run:
            db.commit()
        from app.services.official_map_locations import clear_official_map_slugs_cache

        clear_official_map_slugs_cache()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import platform location CSV dumps")
    parser.add_argument("--s95-csv", type=Path, default=DEFAULT_S95_CSV)
    parser.add_argument("--five-verst-csv", type=Path, default=DEFAULT_FIVE_VERST_CSV)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--import-db", action="store_true")
    args = parser.parse_args()

    if not args.s95_csv.is_file():
        print(f"S95 CSV not found: {args.s95_csv}", file=sys.stderr)
        return 1
    if not args.five_verst_csv.is_file():
        print(f"5verst CSV not found: {args.five_verst_csv}", file=sys.stderr)
        return 1

    if not args.import_db and not args.dry_run:
        parser.error("Specify --import-db or --dry-run")

    stats = import_to_db(
        s95_csv=args.s95_csv,
        five_verst_csv=args.five_verst_csv,
        dry_run=args.dry_run,
    )
    for platform_code, platform_stats in stats.items():
        print(platform_code, platform_stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
