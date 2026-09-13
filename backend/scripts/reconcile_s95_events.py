#!/usr/bin/env python3
"""Разовая сверка состава и нумерации событий S95 со списками площадок.

То же, что делает еженедельная задача s95_sync.reconcile_events, но руками и с
возможностью посмотреть вхолостую. Нужен, когда расхождение уже нашли и чинить
надо сейчас, не дожидаясь вторника.

Запуск:
    docker compose exec api python scripts/reconcile_s95_events.py --dry-run
    docker compose exec api python scripts/reconcile_s95_events.py --slug novgorod
    make prod-run ARGS="scripts/reconcile_s95_events.py"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.sync.s95_events_reconcile import reconcile_s95_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Откатить в конце, ничего не менять.")
    parser.add_argument("--slug", default=None, help="Только одна площадка.")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        result = reconcile_s95_events(db, only_slug=args.slug, apply=not args.dry_run)
        print(f"площадок сверено: {result.locations_checked}")
        print(f"номеров исправлено: {result.numbers_fixed}")
        print(f"убрано событий, которых нет у источника: {result.phantoms_deleted}")
        print(f"снято кросслинков: {result.crosslinks_released}")
        if result.missing_protocols:
            print(f"\nне хватает протоколов ({len(result.missing_protocols)}) — их заберёт синк:")
            for item in result.missing_protocols[:20]:
                print(f"   {item}")
        if result.kept_with_results:
            print(f"\nу источника нет, но у нас с результатами ({len(result.kept_with_results)}) — разбирать руками:")
            for item in result.kept_with_results:
                print(f"   {item}")
        if result.errors:
            print(f"\nошибки ({len(result.errors)}):")
            for item in result.errors[:20]:
                print(f"   {item}")

        # Коммитить/откатывать здесь нечего: сверка пишет по площадкам сама
        # (транзакцию приходится отпускать перед каждым походом в сеть), а в
        # режиме --dry-run не пишет вовсе.
        print("\nDRY-RUN: ничего не изменено." if args.dry_run else "\nПрименено.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
