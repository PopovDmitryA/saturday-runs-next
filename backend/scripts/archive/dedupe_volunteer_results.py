#!/usr/bin/env python3
"""Remove duplicate volunteer rows (same participant, date, location, role)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Participant, Platform
from app.sync import upsert
from app.sync.upsert import dedupe_participant_volunteer_results

SUPPORTED = ("five_verst", "s95", "parkrun")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dedupe volunteer results in DB")
    parser.add_argument("--platform", choices=[*SUPPORTED, "all"], default="all")
    parser.add_argument("--participant-id", help="Limit to one participant UUID")
    args = parser.parse_args()

    participant_id = UUID(args.participant_id) if args.participant_id else None
    platforms = list(SUPPORTED) if args.platform == "all" else [args.platform]

    db = get_session_factory()()
    try:
        total_deleted = 0
        for platform_code in platforms:
            platform = upsert.get_platform(db, platform_code)
            if participant_id is not None:
                participant_ids = [participant_id]
            else:
                participant_ids = [
                    row[0]
                    for row in db.query(Participant.id).filter(Participant.platform_id == platform.id).all()
                ]
            deleted = 0
            for pid in participant_ids:
                deleted += dedupe_participant_volunteer_results(db, platform.id, pid)
            db.commit()
            total_deleted += deleted
            print(f"{platform_code}: deleted={deleted}", flush=True)
        print(f"total_deleted={total_deleted}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
