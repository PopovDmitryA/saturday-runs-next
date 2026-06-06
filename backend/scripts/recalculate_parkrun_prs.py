#!/usr/bin/env python3
"""One-time backfill: recalculate parkrun personal records from finish times."""
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalculate parkrun is_pr flags in DB")
    parser.add_argument(
        "--participant-id",
        help="Limit to one participant UUID (default: all parkrun participants)",
    )
    args = parser.parse_args()

    participant_id = UUID(args.participant_id) if args.participant_id else None
    db = get_session_factory()()
    try:
        stats = recalculate_personal_records(db, "parkrun", participant_id=participant_id)
        db.commit()
        print(
            f"participants_touched={stats['participants_touched']} "
            f"runs_updated={stats['runs_updated']} pr_runs={stats['pr_runs']}"
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
