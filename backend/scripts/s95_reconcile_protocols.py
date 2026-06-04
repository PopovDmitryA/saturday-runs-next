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
from app.sync.s95_reconcile import ReconcileProtocolsOptions, plan_stale_protocol_reconcile, reconcile_stale_protocols


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Re-fetch stale S95 activity protocols")
    parser.add_argument("--dry-run", action="store_true", help="Only plan candidates, do not fetch")
    parser.add_argument(
        "--limit",
        type=int,
        default=settings.s95_reconcile_batch_limit,
        help="Max protocols to reconcile in one run",
    )
    parser.add_argument(
        "--min-age-days",
        type=int,
        default=settings.s95_reconcile_min_check_interval_days,
        help="Re-check protocols older than N days",
    )
    parser.add_argument("--slug", default=None, help="Limit reconcile to one location slug")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        if args.dry_run:
            candidates = plan_stale_protocol_reconcile(
                db,
                limit=args.limit,
                min_check_interval_days=args.min_age_days,
                location_slug=args.slug,
            )
            payload = {
                "candidates_total": len(candidates),
                "planned": [
                    {
                        "external_event_key": item.external_event_key,
                        "location": item.location_external_key,
                        "event_date": item.event_date.isoformat(),
                        "reason": item.reason.value,
                    }
                    for item in candidates
                ],
            }
        else:
            result = reconcile_stale_protocols(
                db,
                ReconcileProtocolsOptions(
                    limit=args.limit,
                    min_check_interval_days=args.min_age_days,
                    location_slug=args.slug,
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
