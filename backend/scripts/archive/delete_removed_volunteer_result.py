#!/usr/bin/env python3
"""One-off: delete volunteer result that was removed from 5verst profile.

Record: 06.06.2026 Долгопрудный "Хранение и доставка оборудования" for participant 790056508.
Confirmed absent from https://5verst.ru/userstats/790056508/ on 2026-06-26.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import VolunteerResult

TARGET_ID = UUID("85005b59-9ef2-42c1-9a63-fba722bba1ca")


def main() -> int:
    db = get_session_factory()()
    try:
        row = db.query(VolunteerResult).filter(VolunteerResult.id == TARGET_ID).one_or_none()
        if row is None:
            print("Record not found — already deleted.")
            return 0
        print(f"Deleting: {row.external_result_key}")
        db.delete(row)
        db.commit()
        print("Done.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
