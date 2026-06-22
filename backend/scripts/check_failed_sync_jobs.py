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
        rows = db.execute(
            text(
                """
                SELECT sj.created_at, sj.status, sj.trigger, sj.error_message,
                       u.display_name, COALESCE(p.code, 'all') AS platform_code
                FROM sync_jobs sj
                JOIN users u ON u.id = sj.user_id
                LEFT JOIN platform_links pl ON pl.id = sj.platform_link_id
                LEFT JOIN platforms p ON p.id = pl.platform_id
                WHERE sj.status = 'failed'
                  AND sj.created_at >= NOW() - INTERVAL '7 days'
                ORDER BY sj.created_at DESC
                LIMIT 30
                """
            )
        ).all()
        for row in rows:
            print("---")
            print(f"time={row[0]} user={row[4]} platform={row[5]} trigger={row[2]}")
            print(f"error={row[3]}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
