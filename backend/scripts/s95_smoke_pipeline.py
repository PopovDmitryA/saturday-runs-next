#!/usr/bin/env python3
"""Sequential S95 smoke: one fetch at a time via coordinator (Redis lock + rate limit)."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.sync.s95_activities_sync import sync_recent_activities
from app.sync.s95_athletes_sync import sync_s95_athlete
from app.sync.s95_location_sync import pick_random_location, sync_s95_event_location


def main() -> int:
    parser = argparse.ArgumentParser(description="S95 smoke pipeline (linear fetches)")
    parser.add_argument("--activity-protocols", type=int, default=2)
    parser.add_argument("--location-protocols", type=int, default=1)
    parser.add_argument(
        "--athletes",
        default="5207,6514,296,4460,1491",
        help="Comma-separated athlete IDs",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    athlete_ids = [x.strip() for x in args.athletes.split(",") if x.strip()]
    payload: dict[str, object] = {"steps": []}

    db = get_session_factory()()
    try:
        print("=== Step 1: recent activities + protocols (new/changed only) ===", flush=True)
        act = sync_recent_activities(db, protocol_fetch_limit=args.activity_protocols)
        payload["steps"].append({"name": "recent_activities", "result": asdict(act)})
        print(asdict(act), flush=True)

        loc_key, loc_name, loc_url = pick_random_location()
        print(f"=== Step 2–3: location {loc_key} ({loc_name}) summary + coords + protocol ===", flush=True)
        loc = sync_s95_event_location(
            db,
            location_external_key=loc_key,
            location_name=loc_name,
            location_source_url=loc_url,
            summaries_limit=10,
            protocol_fetch_limit=args.location_protocols,
        )
        payload["steps"].append(
            {
                "name": "random_location",
                "location": {"key": loc_key, "name": loc_name, "url": loc_url},
                "result": asdict(loc),
            }
        )
        print(asdict(loc), flush=True)

        print("=== Step 4: athlete profiles (friends + awards) ===", flush=True)
        athlete_results = []
        for athlete_id in athlete_ids:
            print(f"--- athlete {athlete_id} ---", flush=True)
            res = sync_s95_athlete(db, athlete_id, retry_on_empty=True)
            athlete_results.append(asdict(res))
            print(asdict(res), flush=True)
        payload["steps"].append({"name": "athletes", "results": athlete_results})

    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
