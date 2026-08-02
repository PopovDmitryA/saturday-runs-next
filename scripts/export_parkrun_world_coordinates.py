#!/usr/bin/env python3
"""Выгрузка координат площадок parkrun мира в data/parkrun_world_coordinates.json.

Источник — SQLite каталога соседнего репозитория parkrun-monitoring
(~/Projects/parkrun-monitoring/data/parkrun.db), который еженедельно обходит
мировой список площадок. Здесь нужны только координаты: у зарубежных
parkrun-локаций в нашей БД их нет ни у одной, а без них «дальность стартов от
дома» молча считает поездку в Лондон нулём километров.

Результат коммитится в репозиторий, чтобы бэкфилл на проде не зависел от
доступа к соседнему репо (см. backend/scripts/backfill_parkrun_coordinates.py).

    ./scripts/export_parkrun_world_coordinates.py
    ./scripts/export_parkrun_world_coordinates.py --source /path/to/parkrun.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path.home() / "Projects" / "parkrun-monitoring" / "data" / "parkrun.db"
DEFAULT_TARGET = ROOT / "data" / "parkrun_world_coordinates.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="SQLite каталога parkrun-monitoring")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Куда писать JSON")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Не найден каталог parkrun-monitoring: {args.source}", file=sys.stderr)
        return 1

    connection = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT eventname, long_name, latitude, longitude
            FROM events
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY eventname
            """
        ).fetchall()
    finally:
        connection.close()

    events = [
        {
            "slug": str(eventname).strip(),
            "name": (long_name or "").strip() or None,
            "latitude": round(float(latitude), 6),
            "longitude": round(float(longitude), 6),
        }
        for eventname, long_name, latitude, longitude in rows
    ]

    payload = {
        "source": "parkrun-monitoring (data/parkrun.db, таблица events)",
        "count": len(events),
        "events": events,
    }
    args.target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"{args.target}: {len(events)} площадок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
