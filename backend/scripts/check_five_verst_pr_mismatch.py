#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.session import get_session_factory


def main() -> int:
    db = get_session_factory()()
    try:
        mismatch = db.execute(
            text(
                """
                SELECT count(*) FROM run_results rr
                JOIN events e ON e.id = rr.event_id
                JOIN platforms p ON p.id = e.platform_id
                WHERE p.code = 'five_verst'
                  AND rr.is_pr = false
                  AND rr.achievement_labels::text ILIKE '%личный рекорд%'
                """
            )
        ).scalar()
        pr_total = db.execute(
            text(
                """
                SELECT count(*) FROM run_results rr
                JOIN events e ON e.id = rr.event_id
                JOIN platforms p ON p.id = e.platform_id
                WHERE p.code = 'five_verst' AND rr.is_pr
                """
            )
        ).scalar()
        print(f"platform_mismatch={mismatch} platform_is_pr_total={pr_total}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
