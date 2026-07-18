#!/usr/bin/env python3
"""Re-fetch one S95 activity protocol via the JSON API and upsert run_results
(e.g. after a parser fix or to repair a specific event)."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Location
from app.s95.api_client import S95ApiActivityRef
from app.sync import upsert
from app.sync.s95_protocol_api import upsert_activity_protocol_api

ACTIVITY_ID_RE = re.compile(r"/activities/(\d+)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("activity_url", help="https://s95.ru/activities/4124 (HTML or .json, either works)")
    parser.add_argument("--location-key", default="zil")
    parser.add_argument("--event-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--event-number", type=int, default=None)
    args = parser.parse_args()

    from datetime import date

    match = ACTIVITY_ID_RE.search(args.activity_url)
    if match is None:
        print("Expected a URL like https://s95.ru/activities/<id>", file=sys.stderr)
        return 1
    activity_id = match.group(1)
    domain_match = re.match(r"https?://[^/]+", args.activity_url)
    domain = domain_match.group(0) if domain_match else "https://s95.ru"
    activity_json_url = f"{domain}/activities/{activity_id}.json"

    event_date = date.fromisoformat(args.event_date)
    db = get_session_factory()()
    platform = upsert.get_platform(db, "s95")
    location = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == args.location_key,
        )
        .one()
    )

    activity_ref = S95ApiActivityRef(date=event_date.isoformat(), url=activity_json_url)
    result = upsert_activity_protocol_api(
        db,
        platform,
        location,
        activity_ref,
        event_number=args.event_number,
    )
    db.commit()
    print(
        f"created={result.created} changed={result.changed} "
        f"run_results_count={result.run_results_count} volunteer_results_count={result.volunteer_results_count}"
    )
    print(asdict(result))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
