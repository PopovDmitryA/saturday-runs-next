#!/usr/bin/env python3
"""One-time backfill: recalculate is_global_pr/is_location_pr from finish times.

Athlete-wide records (span every platform the user has linked) — run this after
applying migration 046 (adds the columns) so existing runs get real values instead
of the server_default false.
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
from app.models import PlatformLink
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.personal_record_service import recalculate_cross_platform_personal_records

COMMIT_EVERY = 200


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recalculate is_global_pr/is_location_pr flags from finish times"
    )
    parser.add_argument("--user-id", help="Limit to one User UUID")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        catalog_index = LocationCatalogIndex(db)

        if args.user_id:
            user_ids = [UUID(args.user_id)]
        else:
            user_ids = [row[0] for row in db.query(PlatformLink.user_id).distinct().all()]

        users_touched = 0
        runs_updated = 0
        global_pr_runs = 0
        location_pr_runs = 0

        for index, user_id in enumerate(user_ids, start=1):
            stats = recalculate_cross_platform_personal_records(db, user_id, catalog_index=catalog_index)
            users_touched += 1
            runs_updated += stats["runs_updated"]
            global_pr_runs += stats["global_pr_runs"]
            location_pr_runs += stats["location_pr_runs"]
            if index % COMMIT_EVERY == 0:
                db.commit()

        db.commit()
        print(
            f"users_touched={users_touched} runs_updated={runs_updated} "
            f"global_pr_runs={global_pr_runs} location_pr_runs={location_pr_runs}",
            flush=True,
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
