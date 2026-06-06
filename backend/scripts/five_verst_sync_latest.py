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

from app.config import get_settings
from app.db.session import get_session_factory
from app.sync.five_verst_latest import LatestResultsSyncOptions, plan_latest_results_sync, sync_latest_results
from app.platform_adapters.five_verst import bulk_parser


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Sync 5verst latest results from /results/latest/")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only fetch and classify rows; do not write to database",
    )
    parser.add_argument(
        "--protocols",
        type=int,
        default=None,
        help="Max protocols to fetch per run (default: all new/changed/missing)",
    )
    parser.add_argument(
        "--update-limit",
        type=int,
        default=settings.five_verst_sync_latest_update_limit,
        help="Max latest summaries to upsert per run (0 = all pending)",
    )
    parser.add_argument(
        "--no-ensure-locations",
        action="store_true",
        help="Do not fetch/create missing locations before upserting summaries",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        if args.dry_run:
            summaries, _ = bulk_parser.fetch_latest_results()
            plan = plan_latest_results_sync(db, summaries)
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
                        "event_number": item.summary.event_number,
                        "action": item.action.value,
                    }
                    for item in plan
                    if item.action.value != "unchanged"
                ][:15],
            }
        else:
            result = sync_latest_results(
                db,
                LatestResultsSyncOptions(
                    dry_run=False,
                    update_limit=None if args.update_limit == 0 else args.update_limit,
                    protocol_fetch_limit=args.protocols if args.protocols and args.protocols > 0 else None,
                    ensure_locations=not args.no_ensure_locations,
                    fetch_all_protocols_on_change=settings.five_verst_fetch_all_protocols_on_change,
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
