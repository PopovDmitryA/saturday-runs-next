#!/usr/bin/env python3
"""One-time backfill: recalculate is_pr from finish times (5verst, s95, parkrun)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.personal_record_service import recalculate_personal_records

SUPPORTED = ("five_verst", "s95", "parkrun")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalculate personal record flags from finish times")
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
            stats = recalculate_personal_records(
                db,
                platform_code,
                participant_id=participant_id,
            )
            db.commit()
            print(
                f"{platform_code}: participants_touched={stats['participants_touched']} "
                f"runs_updated={stats['runs_updated']} pr_runs={stats['pr_runs']}",
                flush=True,
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
