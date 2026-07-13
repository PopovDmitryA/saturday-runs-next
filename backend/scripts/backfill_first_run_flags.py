#!/usr/bin/env python3
"""One-time backfill: derive is_first_run / is_first_run_at_location for s95, parkrun, runpark.

five_verst (and the legacy s95 HTML protocol path) already populate these fields from
achievement badges parsed off the source site — that data is left untouched. This script
covers the platforms where the field was never populated: s95's live JSON API sync path,
parkrun, and runpark.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.personal_record_service import FIRST_RUN_DERIVED_PLATFORMS, recalculate_first_run_flags

SUPPORTED = tuple(sorted(FIRST_RUN_DERIVED_PLATFORMS))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive is_first_run / is_first_run_at_location from chronological run order"
    )
    parser.add_argument(
        "--platform",
        choices=[*SUPPORTED, "all"],
        default="all",
        help="Platform to recalculate (default: all)",
    )
    parser.add_argument(
        "--participant-id",
        help="Limit to one participant UUID",
    )
    args = parser.parse_args()

    participant_id = UUID(args.participant_id) if args.participant_id else None
    platforms = list(SUPPORTED) if args.platform == "all" else [args.platform]

    db = get_session_factory()()
    try:
        for platform_code in platforms:
            stats = recalculate_first_run_flags(
                db,
                platform_code,
                participant_id=participant_id,
            )
            db.commit()
            print(
                f"{platform_code}: participants_touched={stats['participants_touched']} "
                f"runs_updated={stats['runs_updated']}",
                flush=True,
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
