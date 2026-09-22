#!/usr/bin/env python3
"""Backfill events.source_url for RunPark events with the protocol page URL.

Builds ``https://runpark.ru/Places/{slug}/event/{n}`` from the location's
runpark_location_mappings entry (public_url is the authoritative slug — the
location external_key may carry a ``runpark-`` prefix or diverge entirely) plus
the stored event_number. Idempotent: only touches rows whose source_url differs.

Run:
    docker compose exec api python scripts/backfill_runpark_event_source_urls.py
    make prod-run ARGS="scripts/backfill_runpark_event_source_urls.py --dry-run"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Event, Location, Platform, RunparkLocationMapping
from app.runpark.mappings import runpark_protocol_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Не писать в БД, только посчитать.")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        rows = (
            db.query(Event, RunparkLocationMapping)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Location, Location.id == Event.location_id)
            .join(
                RunparkLocationMapping,
                RunparkLocationMapping.runpark_location_row_id == Location.id,
            )
            .filter(
                Platform.code == "runpark",
                Event.event_number.isnot(None),
            )
            .all()
        )

        updated = 0
        skipped_no_url = 0
        for event, mapping in rows:
            url = runpark_protocol_url(mapping.public_url, mapping.runpark_slug, event.event_number)
            if url is None:
                skipped_no_url += 1
                continue
            if url != (event.source_url or ""):
                event.source_url = url
                updated += 1

        if args.dry_run:
            db.rollback()
            print(f"[dry-run] candidates={len(rows)} would_update={updated} skipped_no_url={skipped_no_url}")
        else:
            db.commit()
            print(f"candidates={len(rows)} events_updated={updated} skipped_no_url={skipped_no_url}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
