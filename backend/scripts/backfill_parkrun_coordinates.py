#!/usr/bin/env python3
"""Проставить координаты parkrun-локациям, у которых их нет.

Источник — data/parkrun_world_coordinates.json (см.
app.services.parkrun_world_coordinates). Трогаем только строки, где широта или
долгота пусты: у русских площадок координаты выверены вручную, перетирать их
мировым каталогом нельзя.

    make prod-run ARGS="scripts/backfill_parkrun_coordinates.py --dry-run --pretty"
    CONFIRM_PROD=1 make prod-run ARGS="scripts/backfill_parkrun_coordinates.py"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Location, Platform
from app.services.parkrun_world_coordinates import (
    coordinates_for_parkrun_slug,
    parkrun_world_coordinates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill coordinates for parkrun locations")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не писать, только посчитать")
    parser.add_argument("--limit", type=int, default=0, help="Максимум строк за прогон (0 — все)")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    catalog = parkrun_world_coordinates()
    if not catalog:
        print(json.dumps({"error": "нет data/parkrun_world_coordinates.json"}, ensure_ascii=False), file=sys.stderr)
        return 1

    db = get_session_factory()()
    updated = 0
    skipped: list[str] = []
    try:
        query = (
            db.query(Location)
            .join(Platform, Location.platform_id == Platform.id)
            .filter(
                Platform.code == "parkrun",
                (Location.latitude.is_(None)) | (Location.longitude.is_(None)),
            )
            .order_by(Location.external_key.asc())
        )
        if args.limit:
            query = query.limit(args.limit)
        for location in query.all():
            coordinates = coordinates_for_parkrun_slug(location.external_key)
            if coordinates is None:
                skipped.append(location.external_key)
                continue
            location.latitude, location.longitude = coordinates
            updated += 1
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception as exc:
        db.rollback()
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    payload = {
        "catalog_size": len(catalog),
        "updated": updated,
        "skipped": len(skipped),
        "skipped_examples": skipped[:20],
        "dry_run": args.dry_run,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
