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
from app.sync.global_sync import LocationSyncOptions, sync_location


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Sync one 5verst location into the database")
    add_bootstrap_args(parser)
    parser.add_argument("--slug", required=True, help="Park slug")
    parser.add_argument("--summaries", type=int, default=5, help="Max summaries from /results/all/ (0 = all)")
    parser.add_argument(
        "--protocols",
        type=int,
        default=1,
        help="How many changed summaries to fetch full protocols for (0 = skip, ignored when --fetch-all)",
    )
    parser.add_argument(
        "--fetch-all",
        action="store_true",
        help="Fetch protocols for all changed/missing summaries (ignore --protocols limit)",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    settings = get_settings()

    summaries_limit = None if args.summaries == 0 else args.summaries
    db = get_session_factory()()
    try:
        result = sync_location(
            db,
            LocationSyncOptions(
                location_slug=args.slug,
                summaries_limit=summaries_limit,
                protocol_fetch_limit=args.protocols,
                fetch_all_protocols_on_change=args.fetch_all or settings.five_verst_fetch_all_protocols_on_change,
            ),
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    payload = asdict(result)
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0 if not result.errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
