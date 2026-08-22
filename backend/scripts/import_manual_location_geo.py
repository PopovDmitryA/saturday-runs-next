#!/usr/bin/env python3
"""Проставить локациям координаты (и название) из data/locations/manual_geo.json.

Нужен для площадок, которым координаты неоткуда взять: автогеокодер их не
находит, а у parkrun-площадок без преемника в действующих системах нет и
связки, от которой унаследовать точку.

Слаг сравнивается нормализованно (как normalize_location_slug), поэтому одна
запись накрывает оба написания площадки — shuvalovsky-park и shuvalovskypark.

По умолчанию только показывает, что изменится. Запись — с --apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models import Location, Platform
from app.services.location_catalog_service import normalize_location_slug

DEFAULT_JSON = ROOT.parent / "data" / "locations" / "manual_geo.json"


def _load(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("locations", [])
    for entry in entries:
        missing = [key for key in ("platform", "slug", "latitude", "longitude") if key not in entry]
        if missing:
            raise SystemExit(f"В записи {entry!r} не хватает полей: {', '.join(missing)}")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help=f"Файл с координатами (по умолчанию {DEFAULT_JSON})")
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только показать)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Перезаписывать уже заполненные координаты (по умолчанию такие пропускаются)",
    )
    args = parser.parse_args()

    if not args.json.exists():
        raise SystemExit(f"Файл не найден: {args.json}")

    entries = _load(args.json)
    session_factory = get_session_factory()
    updated = skipped = unmatched = 0

    with session_factory() as db:
        for entry in entries:
            platform_code = str(entry["platform"])
            wanted = normalize_location_slug(str(entry["slug"]))
            rows = db.execute(
                select(Location)
                .join(Platform, Location.platform_id == Platform.id)
                .where(
                    Platform.code == platform_code,
                    func.regexp_replace(func.lower(Location.external_key), "[^a-z0-9]", "", "g") == wanted,
                )
            ).scalars().all()

            if not rows:
                print(f"  НЕ НАЙДЕНО: {platform_code}/{entry['slug']}")
                unmatched += 1
                continue

            for location in rows:
                has_coords = location.latitude is not None and location.longitude is not None
                if has_coords and not args.overwrite:
                    print(f"  пропуск (координаты уже есть): {platform_code}/{location.external_key}")
                    skipped += 1
                    continue
                changes = [f"координаты → {entry['latitude']}, {entry['longitude']}"]
                if args.apply:
                    location.latitude = float(entry["latitude"])  # type: ignore[arg-type]
                    location.longitude = float(entry["longitude"])  # type: ignore[arg-type]
                name = entry.get("name")
                if name and location.name != name:
                    changes.append(f"название «{location.name}» → «{name}»")
                    if args.apply:
                        location.name = str(name)
                city = entry.get("city")
                if city and location.city != city:
                    changes.append(f"город «{location.city}» → «{city}»")
                    if args.apply:
                        location.city = str(city)
                print(f"  {platform_code}/{location.external_key}: " + "; ".join(changes))
                updated += 1

        if args.apply:
            db.commit()

    print(
        f"\nобновлено строк: {updated}, пропущено: {skipped}, не найдено записей: {unmatched}"
        + ("" if args.apply else "  (пробный прогон, ничего не записано — добавь --apply)")
    )
    return 1 if unmatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
