"""Backfill locations.region для локаций без региона, у которых есть

каталожная связка (location_catalog) с другой платформенной локацией,
где регион уже известен. В основном чинит parkrun-локации — их source
страницы не отдают регион, а связанная 5 вёрст/S95/RunPark локация его
знает.

Запуск (по умолчанию dry-run, ничего не пишет):
    docker compose exec api python scripts/backfill_location_region.py
    docker compose exec api python scripts/backfill_location_region.py --apply
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

from app.db.session import get_session_factory  # noqa: E402
from app.models import Location  # noqa: E402
from app.services.location_catalog_service import backfill_region_from_catalog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (по умолчанию dry-run)")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        locations = db.query(Location).filter(Location.region.is_(None)).all()
        updated = 0
        for location in locations:
            before = location.region
            if backfill_region_from_catalog(db, location):
                updated += 1
                print(f"{location.id}  {location.name!r}: {before!r} -> {location.region!r}")
        print(f"Всего кандидатов: {len(locations)}, обновлено: {updated}")
        if args.apply:
            db.commit()
            print("Изменения записаны.")
        else:
            db.rollback()
            print("Dry-run: изменения НЕ записаны (запустите с --apply).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
