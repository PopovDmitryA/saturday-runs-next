#!/usr/bin/env python3
"""Load the RunPark history of locations that moved on to a primary platform.

Жуковский и Мытищи прошли путь parkrun → runpark → 5 вёрст. В импорте маппингов
они получили decision='transitional' (формулировка «пока под вопросом» приехала из
файла-источника), а Location-строку под runpark-период заводят только для
show_on_map — поэтому их результаты не легли никуда: ни под runpark, ни под 5 вёрст.

Скрипт достраивает им ровно ту же конфигурацию, что у 30 уже работающих
transitioned_to_primary локаций (Location `runpark-<slug>`, show_on_map=false,
is_official_map=false), и подтягивает историю точечным синком.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Location, Platform, RunparkLocationMapping
from app.sync.runpark_global_sync import PLATFORM_CODE, sync_runpark_batch

# Первый 5км-старт в RunPark — 12.03.2022; берём с запасом.
DEFAULT_SINCE = date(2022, 1, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enable transitional RunPark locations and backfill them")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would change")
    parser.add_argument("--no-sync", action="store_true", help="Only fix mappings, skip the backfill sync")
    parser.add_argument("--since", default=DEFAULT_SINCE.isoformat(), help="Backfill start date (YYYY-MM-DD)")
    args = parser.parse_args()
    since = date.fromisoformat(args.since)

    Session = get_session_factory()
    with Session() as db:
        runpark_platform = db.query(Platform).filter(Platform.code == PLATFORM_CODE).one()

        # Кандидаты: замаплены на целевую площадку, но Location под runpark нет —
        # значит их история сейчас не грузится вообще.
        candidates = (
            db.query(RunparkLocationMapping)
            .filter(
                RunparkLocationMapping.decision == "transitional",
                RunparkLocationMapping.runpark_location_row_id.is_(None),
                RunparkLocationMapping.matched_location_id.isnot(None),
            )
            .all()
        )
        if not candidates:
            print("Нечего включать: все transitional-локации уже настроены.", flush=True)
            return 0

        print(f"Локаций к включению: {len(candidates)}", flush=True)
        for m in candidates:
            print(f"  · {m.runpark_name} (slug={m.runpark_slug}) → пара {m.matched_platform}:{m.matched_external_key}")

        if args.dry_run:
            print("\n--dry-run: изменений нет", flush=True)
            return 0

        location_ids: set[str] = set()
        for m in candidates:
            external_key = f"runpark-{m.runpark_slug}"
            matched = db.query(Location).filter(Location.id == m.matched_location_id).one_or_none()
            row = (
                db.query(Location)
                .filter(Location.platform_id == runpark_platform.id, Location.external_key == external_key)
                .one_or_none()
            )
            if row is None:
                row = Location(
                    platform_id=runpark_platform.id,
                    external_key=external_key,
                    name=m.display_name or m.runpark_name,
                    country=m.country or (matched.country if matched else None),
                    city=m.city or (matched.city if matched else None),
                    region=m.region or (matched.region if matched else None),
                    latitude=m.latitude,
                    longitude=m.longitude,
                    # Площадка больше не действует под runpark — на карту не выводим.
                    is_official_map=False,
                    is_paused=bool(m.is_paused),
                    source_url=m.public_url,
                )
                db.add(row)
                db.flush()
                print(f"  + Location {external_key} ({row.name})", flush=True)
            m.runpark_location_row_id = row.id
            m.decision = "transitioned_to_primary"
            location_ids.add(m.runpark_location_id.upper())
        db.commit()
        print(f"\nМаппинги обновлены: decision=transitioned_to_primary, Location проставлен.", flush=True)

        if args.no_sync:
            print("--no-sync: синк пропущен", flush=True)
            return 0

        print(f"\nТочечный синк с {since}…", flush=True)
        result = sync_runpark_batch(db, since, only_location_ids=location_ids)
        print(f"  событий найдено:   {result.events_total}")
        print(f"  событий залито:    {result.events_upserted}")
        print(f"  результатов:       {result.run_results_upserted}")
        print(f"  волонтёров:        {result.volunteer_results_upserted}")
        if result.errors:
            print(f"  ОШИБКИ ({len(result.errors)}):")
            for err in result.errors[:10]:
                print(f"    ! {err}")
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
