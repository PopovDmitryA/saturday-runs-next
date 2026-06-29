#!/usr/bin/env python3
"""One-time full backfill of S95 protocols via the JSON API.

Iterates every protocol of every location across s95.ru / s95.by / s95.rs, upserts
results, and records the reconciled-through watermark. Idempotent — unchanged protocols
are skipped cheaply, so it is safe to re-run if interrupted.
"""
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
from app.sync.s95_global_sync_api import (
    full_backfill,
    reconcile_protocols_for_date,
    sync_new_protocols,
)
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="S95 protocol sync via JSON API")
    add_bootstrap_args(parser)
    parser.add_argument(
        "--mode",
        choices=["backfill", "new", "reconcile"],
        default="backfill",
        help="backfill = all protocols (one-time); new = only missing; reconcile = a given date",
    )
    parser.add_argument("--limit-per-location", type=int, default=None)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD for --mode reconcile")
    parser.add_argument("--delay-min", type=float, default=10.0, help="Min seconds between fetches (backfill)")
    parser.add_argument("--delay-max", type=float, default=40.0, help="Max seconds between fetches (backfill)")
    parser.add_argument("--reset-dates", action="store_true", help="Clear protocol check dates before backfill")
    parser.add_argument("--no-resume", action="store_true", help="Do not skip already-checked protocols")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        if args.mode == "backfill":
            result = full_backfill(
                db,
                limit_per_location=args.limit_per_location,
                delay_min_sec=args.delay_min,
                delay_max_sec=args.delay_max,
                resume=not args.no_resume,
                reset_dates=args.reset_dates,
            )
        elif args.mode == "new":
            result = sync_new_protocols(db)
        else:
            from datetime import date

            if not args.date:
                print("--date is required for --mode reconcile", file=sys.stderr)
                return 1
            target = date.fromisoformat(args.date)
            result = reconcile_protocols_for_date(db, target)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    indent = 2 if args.pretty else None
    print(json.dumps(asdict(result), ensure_ascii=False, indent=indent))
    return 0 if not result.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
