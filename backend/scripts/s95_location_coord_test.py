#!/usr/bin/env python3
"""Sync 1–2 S95 locations to trigger coordinate Telegram requests."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import joinedload

from app.db.session import get_session_factory
from app.models import Location, LocationCoordinateRequest, Platform
from app.sync.s95_location_sync import sync_s95_event_location

LOCATIONS = [
    ("gatchina", "Гатчина", "https://s95.ru/events/gatchina"),
    ("angarskie_prudy", "Ангарские пруды", "https://s95.ru/events/angarskie_prudy"),
]


def main() -> int:
    db = get_session_factory()()
    report: dict[str, object] = {}

    for key, name, url in LOCATIONS:
        print(f"=== sync {key} ===", flush=True)
        result = sync_s95_event_location(
            db,
            location_external_key=key,
            location_name=name,
            location_source_url=url,
            summaries_limit=3,
            protocol_fetch_limit=0,
        )
        report[key] = asdict(result)

    platform = db.query(Platform).filter(Platform.code == "s95").one()
    location_ids = [
        row.id
        for row in db.query(Location.id)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key.in_([k for k, _, _ in LOCATIONS]),
        )
        .all()
    ]
    requests = (
        db.query(LocationCoordinateRequest)
        .options(joinedload(LocationCoordinateRequest.location))
        .filter(LocationCoordinateRequest.location_id.in_(location_ids))
        .order_by(LocationCoordinateRequest.created_at.desc())
        .all()
    )
    report["coordinate_requests"] = [
        {
            "location_key": r.location.external_key,
            "status": r.status,
            "map_url": (r.map_url or "")[:80],
        }
        for r in requests
    ]
    db.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
