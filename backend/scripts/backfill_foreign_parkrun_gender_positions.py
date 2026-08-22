#!/usr/bin/env python3
"""Одноразовый бэкфилл: снять gender_position с зарубежных parkrun-результатов.

Протоколы зарубежного parkrun мы не собираем — от такой площадки в БД лежат
только строки наших же участников, вытащенные из их профилей. В «поле» из одной
строки любой финишёр оказывался первым среди своего пола, и на проде набралось
~158 тыс. таких gender_position (из них ~338 у зарегистрированных пользователей
— именно они лезли в плитку «Победы» зарубежными стартами).

Новые синки такого больше не пишут: recalculate_event_gender_positions чистит
место по полу на зарубежных площадках (см. gender_position_service). Скрипт
приводит в порядок уже накопленное.

Русская площадка определяется связкой с каталогом локаций
(russian_parkrun_location_ids) — тем же правилом, что и в приложении.

Прогон на проде:
    CONFIRM_PROD=1 make prod-run ARGS="scripts/backfill_foreign_parkrun_gender_positions.py"
Сначала посмотреть объём, ничего не меняя:
    make prod-run ARGS="scripts/backfill_foreign_parkrun_gender_positions.py --dry-run"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, update

from app.db.session import get_session_factory
from app.models import Event, Platform, RunResult
from app.services.location_catalog_service import russian_parkrun_location_ids


def _chunk(items: list[UUID], size: int) -> list[list[UUID]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Снять gender_position с результатов зарубежного parkrun"
    )
    parser.add_argument(
        "--chunk-size", type=int, default=500, help="Локаций за один UPDATE (по умолчанию 500)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Только посчитать, ничего не менять")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == "parkrun").one()
        russian_ids = russian_parkrun_location_ids(db)
        location_ids = [
            row[0]
            for row in db.query(Event.location_id)
            .filter(Event.platform_id == platform.id, Event.location_id.isnot(None))
            .distinct()
            .all()
        ]
        foreign_ids = [location_id for location_id in location_ids if location_id not in russian_ids]
        print(
            f"parkrun-локаций с событиями: {len(location_ids)}, "
            f"русских: {len(location_ids) - len(foreign_ids)}, зарубежных: {len(foreign_ids)}"
        )

        started = time.monotonic()
        cleared = 0
        for chunk_index, chunk in enumerate(_chunk(foreign_ids, args.chunk_size), start=1):
            events = (
                db.query(Event.id)
                .filter(Event.platform_id == platform.id, Event.location_id.in_(chunk))
                .scalar_subquery()
            )
            condition = RunResult.event_id.in_(events) & RunResult.gender_position.isnot(None)
            if args.dry_run:
                affected = (
                    db.query(func.count(RunResult.id)).filter(condition).scalar() or 0
                )
            else:
                affected = db.execute(
                    update(RunResult).where(condition).values(gender_position=None)
                ).rowcount
                db.commit()
            cleared += int(affected)
            processed = min(chunk_index * args.chunk_size, len(foreign_ids))
            elapsed = time.monotonic() - started
            print(
                f"  {processed}/{len(foreign_ids)} локаций, {cleared} строк "
                f"{'нашлось' if args.dry_run else 'очищено'} ({elapsed:.0f}s)",
                flush=True,
            )
        print("dry-run: изменений не вносилось" if args.dry_run else "done")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
