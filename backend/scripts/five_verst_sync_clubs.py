#!/usr/bin/env python3
"""Manual run / backfill of the 5verst clubs sync (list registry and club details)."""
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
from app.db.session import get_session_factory


def _dry_run_registry(limit: int | None) -> dict[str, object]:
    from app.platform_adapters.five_verst import clubs_parser

    entries, _ = clubs_parser.fetch_clubs_page()
    if limit is not None:
        entries = entries[:limit]
    return {
        "dry_run": True,
        "entries_total": len(entries),
        "entries": [
            {
                "slug": entry.slug,
                "name": entry.name,
                "members_count": entry.members_count,
                "finishes_count": entry.finishes_count,
                "volunteerings_count": entry.volunteerings_count,
                "row_hash": entry.row_hash,
            }
            for entry in entries
        ],
    }


def _dry_run_details(db, limit: int) -> dict[str, object]:
    from app.models import Club
    from app.sync.five_verst_clubs import PLATFORM_CODE
    from app.sync import upsert

    platform = upsert.get_platform(db, PLATFORM_CODE)
    clubs = (
        db.query(Club)
        .filter(Club.platform_id == platform.id, Club.is_active.is_(True))
        .order_by(
            Club.needs_detail_sync.desc(),
            Club.detail_synced_at.asc().nullsfirst(),
            Club.external_key.asc(),
        )
        .limit(limit)
        .all()
    )
    return {
        "dry_run": True,
        "clubs_planned": [
            {
                "slug": club.external_key,
                "name": club.name,
                "needs_detail_sync": club.needs_detail_sync,
                "detail_synced_at": club.detail_synced_at.isoformat() if club.detail_synced_at else None,
            }
            for club in clubs
        ],
    }


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Sync 5verst clubs from /clubs/ (list and/or details)")
    add_bootstrap_args(parser)
    parser.add_argument("--registry", action="store_true", help="Sync the clubs list page")
    parser.add_argument("--details", action="store_true", help="Sync a batch of club detail pages (rosters)")
    parser.add_argument("--limit", type=int, default=None, help="Registry: first N rows; details: batch size")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be processed, no writes")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    if not args.registry and not args.details:
        parser.error("pass --registry and/or --details")

    from app.config import get_settings
    from app.sync.five_verst_clubs import ClubsRegistrySyncOptions, sync_club_details_batch, sync_clubs_registry

    payload: dict[str, object] = {}
    exit_code = 0
    db = get_session_factory()()
    try:
        if args.registry:
            if args.dry_run:
                payload["registry"] = _dry_run_registry(args.limit)
            else:
                result = sync_clubs_registry(db, ClubsRegistrySyncOptions(limit=args.limit))
                payload["registry"] = asdict(result)
                if result.errors:
                    exit_code = 2
        if args.details:
            limit = args.limit if args.limit is not None else get_settings().five_verst_clubs_batch_limit
            if args.dry_run:
                payload["details"] = _dry_run_details(db, limit)
            else:
                result = sync_club_details_batch(db, limit=limit)
                payload["details"] = asdict(result)
                if result.errors:
                    exit_code = 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        db.close()

    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
