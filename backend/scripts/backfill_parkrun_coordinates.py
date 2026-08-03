#!/usr/bin/env python3
"""Проставить координаты parkrun-локациям, у которых их нет.

Зачем: у зарубежных parkrun-площадок в нашей БД координат нет почти ни у одной
(9 из 2471 на 02.08.2026) — мы заводим их из профилей участников, а не из
каталога площадок. Без координат «дальность стартов от дома» считает поездку в
Лондон нулём километров, то есть теряет ровно самые дальние старты.

Источник — каталог соседнего репозитория parkrun-monitoring (SQLite, таблица
events), который еженедельно обходит мировой список площадок. Данные живут
только в БД: копию каталога в этот репозиторий не кладём, скрипт читает
источник напрямую. Запускать с Мака, где лежит parkrun-monitoring:

    make prod-run ARGS="scripts/backfill_parkrun_coordinates.py --dry-run --pretty"
    CONFIRM_PROD=1 make prod-run ARGS="scripts/backfill_parkrun_coordinates.py"

Слаги матчатся без дефисов: в каталоге parkrun площадка называется
«aachenerweiher», а в наших локациях — «aachener-weiher». По сырому слагу
совпадает 1028 площадок из 2551, по нормализованному — 2226. Оставшиеся 328 —
закрытые площадки, которых в мировом каталоге уже нет; они остаются без
координат и в сумму километров не идут.

Трогаем только строки, где широта или долгота пусты: у русских площадок
координаты выверены вручную, перетирать их мировым каталогом нельзя.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Location, Platform

DEFAULT_SOURCE = Path.home() / "Projects" / "parkrun-monitoring" / "data" / "parkrun.db"


def normalize_slug(value: str | None) -> str:
    """Слаг площадки без дефисов и регистра — общий ключ двух каталогов."""
    if not value:
        return ""
    return value.strip().lower().replace("-", "").replace("_", "")


def load_catalog(source: Path) -> dict[str, tuple[float, float]]:
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT eventname, latitude, longitude
            FROM events
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            """
        ).fetchall()
    finally:
        connection.close()
    catalog: dict[str, tuple[float, float]] = {}
    for eventname, latitude, longitude in rows:
        slug = normalize_slug(eventname)
        if slug:
            catalog[slug] = (float(latitude), float(longitude))
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill coordinates for parkrun locations")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="SQLite каталога parkrun-monitoring",
    )
    parser.add_argument("--dry-run", action="store_true", help="Ничего не писать, только посчитать")
    parser.add_argument("--limit", type=int, default=0, help="Максимум строк за прогон (0 — все)")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if not args.source.exists():
        print(
            json.dumps(
                {"error": f"каталог parkrun-monitoring не найден: {args.source}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    catalog = load_catalog(args.source)
    if not catalog:
        print(json.dumps({"error": "в каталоге нет координат"}, ensure_ascii=False), file=sys.stderr)
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
            coordinates = catalog.get(normalize_slug(location.external_key))
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
        "source": str(args.source),
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
