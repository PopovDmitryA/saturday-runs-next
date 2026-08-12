#!/usr/bin/env python3
"""Прогрев кэша раздела «Локации».

Индекс и страницы локаций кэшируются в Redis с TTL 3 часа
(location_page_service). Холодный расчёт тяжёлый: индекс — агрегация по всем
событиям всех платформ, страница локации — полтора десятка запросов. Без
прогрева первый посетитель после каждого протухания TTL ждёт эти секунды сам.

Скрипт зовёт те же build_*, что и API, поэтому просто наполняет тот же кэш.
Ставить по расписанию чаще, чем TTL (например, раз в 2 часа), и разово после
деплоя.

    python scripts/warm_locations_cache.py            # индекс + все локации
    python scripts/warm_locations_cache.py --index-only
    python scripts/warm_locations_cache.py --limit 20  # для проверки
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.location_page_service import (
    build_location_events,
    build_location_leaders,
    build_location_page,
    build_locations_index,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm up locations section Redis cache")
    parser.add_argument("--index-only", action="store_true", help="Только каталог, без страниц локаций")
    parser.add_argument("--limit", type=int, default=None, help="Прогреть только N первых локаций")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        started = time.monotonic()
        # refresh=True — считаем заново и перезаписываем, иначе прогрев просто
        # прочитал бы ещё живой кэш и ничего не обновил (а use_cache=False,
        # который стоял тут раньше, вообще ничего не клал в Redis).
        index = build_locations_index(db, refresh=True)
        items = list(index.get("items") or [])
        print(f"index: {len(items)} локаций за {time.monotonic() - started:.1f}s", flush=True)

        if args.index_only:
            return 0

        slugs = [str(item["slug"]) for item in items if item.get("slug")]
        if args.limit:
            slugs = slugs[: args.limit]

        failed = 0
        for number, slug in enumerate(slugs, start=1):
            try:
                build_location_page(db, slug, refresh=True)
                build_location_events(db, slug, refresh=True)
                build_location_leaders(db, slug, refresh=True)
            except Exception as exc:  # noqa: BLE001 — одна битая локация не должна ронять прогрев
                failed += 1
                print(f"  ! {slug}: {exc}", flush=True)
            if number % 25 == 0 or number == len(slugs):
                print(f"  {number}/{len(slugs)} ({time.monotonic() - started:.0f}s)", flush=True)

        print(f"готово за {time.monotonic() - started:.0f}s, ошибок: {failed}")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
