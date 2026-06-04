#!/usr/bin/env python3
"""Remove duplicate 5verst run_results (same participant, date, location)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Participant, Platform
from app.sync.upsert import dedupe_five_verst_run_results_in_db


def main() -> int:
    db = get_session_factory()()
    platform = db.query(Platform).filter(Platform.code == "five_verst").one()
    participants = (
        db.query(Participant).filter(Participant.platform_id == platform.id).all()
    )
    total = 0
    for participant in participants:
        removed = dedupe_five_verst_run_results_in_db(db, platform.id, participant.id)
        if removed:
            print(f"{participant.external_user_id}: removed {removed} duplicate runs")
        total += removed
    db.commit()
    print(f"Done. Removed {total} duplicate run_results total.")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
