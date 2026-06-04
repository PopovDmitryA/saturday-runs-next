#!/usr/bin/env python3
"""Run iteration checks 1-4 after smoke fixes."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.s95.fetch.coordinator import fetch_page_html
from app.s95.parsers.location import parse_map_url
from app.sync.s95_activities_sync import sync_recent_activities
from app.sync.s95_athletes_sync import sync_s95_athlete
from app.sync.s95_location_sync import sync_s95_event_location


def main() -> int:
    db = get_session_factory()()
    report: dict[str, object] = {}

    print("=== 1) Incremental activities (expect mostly unchanged) ===", flush=True)
    report["incremental_activities"] = asdict(sync_recent_activities(db, protocol_fetch_limit=2))

    print("=== 2) ZIL location full summary + 1 protocol ===", flush=True)
    report["zil_sync"] = asdict(
        sync_s95_event_location(
            db,
            location_external_key="zil",
            location_name="ЗИЛ",
            location_source_url="https://s95.ru/events/zil",
            summaries_limit=10,
            protocol_fetch_limit=1,
        )
    )

    print("=== 3) Map link parse angarskie ===", flush=True)
    html = fetch_page_html("https://s95.ru/events/angarskie_prudy", reason="iteration_map")
    report["angarskie_map_url"] = parse_map_url(html)

    print("=== 4) Athlete 5207 refresh ===", flush=True)
    report["athlete_5207"] = asdict(sync_s95_athlete(db, "5207", retry_on_empty=True))

    db.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
