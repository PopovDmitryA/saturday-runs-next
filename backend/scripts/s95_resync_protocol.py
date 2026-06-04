#!/usr/bin/env python3
"""Re-fetch one S95 activity protocol and upsert run_results (e.g. after parser fixes)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Event, Location, Platform
from app.s95.fetch import fetch_page_html
from app.s95.parsers.protocol import parse_protocol_html
from app.sync import upsert


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("activity_url", help="https://s95.ru/activities/4124")
    parser.add_argument("--location-key", default="zil")
    parser.add_argument("--location-name", default="ЗИЛ")
    parser.add_argument("--event-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--event-number", type=int, default=None)
    args = parser.parse_args()

    from datetime import date

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
    event = (
        db.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.location_id == location.id,
            Event.event_date == event_date,
        )
        .one_or_none()
    )
    if event is None:
        print("Event not found in DB — sync location summaries first", file=sys.stderr)
        return 1

    html = fetch_page_html(args.activity_url, reason="resync_protocol")
    runs, vols = parse_protocol_html(
        html,
        args.activity_url,
        location_external_key=args.location_key,
        location_name=args.location_name,
        event_date=event_date,
        event_number=args.event_number,
    )
    n_runs = upsert.upsert_run_results(db, event, platform, runs)
    n_vols = upsert.upsert_volunteer_results(db, event, platform, vols)
    pr_count = sum(1 for r in runs if r.is_pr)
    db.commit()
    print(
        f"runs_upserted={n_runs} volunteers_upserted={n_vols} "
        f"parsed_runs={len(runs)} pr_flags={pr_count}"
    )
    for r in runs:
        if r.is_pr:
            print(f"  PR: {r.participant_name} ({r.external_user_id})")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
