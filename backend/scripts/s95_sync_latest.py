#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env
from app.config import get_settings
from app.db.session import get_session_factory
from app.sync.s95_latest import S95LatestSyncOptions, fetch_latest_activity_summaries, sync_s95_latest
from app.sync.s95_summary_plan import plan_event_summaries_sync


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Sync S95 latest activities from /activities")
    add_bootstrap_args(parser)
    parser.add_argument("--dry-run", action="store_true", help="Classify rows without writing to database")
    parser.add_argument(
        "--protocols",
        type=int,
        default=None,
        help="Fetch full protocols for N new events per run",
    )
    parser.add_argument(
        "--update-limit",
        type=int,
        default=None,
        help="Max summaries to upsert per run (0 = all pending)",
    )
    parser.add_argument("--no-ensure-locations", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    settings = get_settings()
    if args.protocols is None:
        args.protocols = settings.s95_sync_protocol_limit
    if args.update_limit is None:
        args.update_limit = settings.s95_sync_latest_update_limit

    db = get_session_factory()()
    try:
        if args.dry_run:
            summaries = fetch_latest_activity_summaries()
            plan = plan_event_summaries_sync(db, summaries)
            payload = {
                "summaries_total": len(summaries),
                "unchanged": sum(1 for item in plan if item.action.value == "unchanged"),
                "new_summaries": sum(1 for item in plan if item.action.value == "new_summary"),
                "changed_summaries": sum(1 for item in plan if item.action.value == "changed_summary"),
                "missing_protocol": sum(1 for item in plan if item.action.value == "missing_protocol"),
                "needs_update": sum(1 for item in plan if item.action.value != "unchanged"),
                "sample_updates": [
                    {
                        "external_event_key": item.summary.external_event_key,
                        "location": item.summary.location_name,
                        "slug": item.summary.location_external_key,
                        "event_date": item.summary.event_date.isoformat(),
                        "activity_id": item.summary.event_number,
                        "action": item.action.value,
                    }
                    for item in plan
                    if item.action.value != "unchanged"
                ][:15],
            }
        else:
            result = sync_s95_latest(
                db,
                S95LatestSyncOptions(
                    dry_run=False,
                    update_limit=None if args.update_limit == 0 else args.update_limit,
                    protocol_fetch_limit=args.protocols,
                    ensure_locations=not args.no_ensure_locations,
                    fetch_all_protocols_on_change=settings.s95_fetch_all_protocols_on_change,
                ),
            )
            payload = asdict(result)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    if args.dry_run:
        return 0
    return 0 if not payload.get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(main())
