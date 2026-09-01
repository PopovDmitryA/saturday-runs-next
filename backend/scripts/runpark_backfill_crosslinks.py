#!/usr/bin/env python3
"""Досвязать dual_load-протоколы RunPark с парой на основной платформе.

Тот же проход, что и суточная задача `runpark_sync.backfill_crosslinks`, но
руками — чтобы разобрать накопившийся хвост, не дожидаясь ночи. Только БД,
без походов в RunPark MSSQL.

    make prod-run ARGS="scripts/runpark_backfill_crosslinks.py --dry-run --pretty"
    CONFIRM_PROD=1 make prod-run ARGS="scripts/runpark_backfill_crosslinks.py --pretty"
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
from app.models import Event, EventCrosslink, Location, Platform, RunparkLocationMapping
from app.sync.runpark_global_sync import backfill_dual_load_crosslinks
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env


def _pending(db) -> list[dict[str, object]]:
    """Что связалось бы: несвязанный протокол RunPark, у которого пара уже есть."""
    rows = (
        db.query(Event, RunparkLocationMapping.runpark_slug, RunparkLocationMapping.matched_location_id)
        .join(
            RunparkLocationMapping,
            RunparkLocationMapping.runpark_location_row_id == Event.location_id,
        )
        .filter(
            RunparkLocationMapping.decision == "dual_load",
            RunparkLocationMapping.matched_location_id.isnot(None),
            Event.is_test_event.is_(False),
            ~db.query(EventCrosslink.id).filter(EventCrosslink.secondary_event_id == Event.id).exists(),
        )
        .order_by(Event.event_date.desc())
        .all()
    )
    pending: list[dict[str, object]] = []
    for event, slug, matched_location_id in rows:
        primary = (
            db.query(Event, Platform.code, Location.external_key)
            .join(Platform, Event.platform_id == Platform.id)
            .join(Location, Event.location_id == Location.id)
            .filter(
                Event.location_id == matched_location_id,
                Event.event_date == event.event_date,
                Event.is_test_event.is_(False),
            )
            .first()
        )
        if primary is None:
            continue  # старт был только у RunPark — связывать не с чем
        primary_event, platform_code, primary_slug = primary
        pending.append(
            {
                "runpark_slug": slug,
                "event_date": event.event_date.isoformat(),
                "runpark_finishers": event.finishers_count,
                "primary_platform": platform_code,
                "primary_slug": primary_slug,
                "primary_finishers": primary_event.finishers_count,
            }
        )
    return pending


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Backfill RunPark dual_load crosslinks")
    add_bootstrap_args(parser)
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что связалось бы")
    parser.add_argument("--limit", type=int, default=500, help="Потолок за прогон")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        pending = _pending(db)
        if args.dry_run:
            payload: dict[str, object] = {"dry_run": True, "would_link": len(pending), "items": pending}
        else:
            linked = backfill_dual_load_crosslinks(db, limit=args.limit)
            db.commit()
            payload = {"dry_run": False, "crosslinks_backfilled": linked, "items": pending[:linked]}
    except Exception as exc:
        db.rollback()
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
