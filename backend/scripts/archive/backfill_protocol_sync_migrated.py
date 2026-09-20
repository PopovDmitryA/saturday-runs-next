#!/usr/bin/env python3
"""Mark legacy-migrated event protocols as fetched (protocol_sync_states.last_protocol_fetched_at).

Migration from five_verst_stats imported run_results but never set ProtocolSyncState.
Without the flag, profile sync re-enqueues full protocol fetches for old Saturdays.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.models import Event, EventSummary, Platform, ProtocolSyncState, RunResult, VolunteerResult
from app.sync import upsert

DEFAULT_BEFORE_DATE = date(2026, 5, 20)
DEFAULT_FETCHED_AT = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
SUPPORTED_PLATFORMS = ("five_verst", "s95")


def _events_needing_backfill(
    db: Session,
    platform: Platform,
    *,
    before_date: date,
) -> list[tuple[Event, object | None, int, int, ProtocolSyncState | None]]:
    """Events with run results, event_date < before_date, missing last_protocol_fetched_at."""
    run_counts = (
        db.query(RunResult.event_id, func.count(RunResult.id).label("run_count"))
        .group_by(RunResult.event_id)
        .subquery()
    )
    volunteer_counts = (
        db.query(VolunteerResult.event_id, func.count(VolunteerResult.id).label("volunteer_count"))
        .group_by(VolunteerResult.event_id)
        .subquery()
    )
    rows = (
        db.query(
            Event,
            EventSummary.id,
            run_counts.c.run_count,
            volunteer_counts.c.volunteer_count,
            ProtocolSyncState,
        )
        .join(run_counts, run_counts.c.event_id == Event.id)
        .outerjoin(volunteer_counts, volunteer_counts.c.event_id == Event.id)
        .outerjoin(EventSummary, EventSummary.event_id == Event.id)
        .outerjoin(ProtocolSyncState, ProtocolSyncState.event_id == Event.id)
        .filter(
            Event.platform_id == platform.id,
            Event.event_date < before_date,
            (ProtocolSyncState.id.is_(None)) | (ProtocolSyncState.last_protocol_fetched_at.is_(None)),
        )
        .order_by(Event.event_date.asc())
        .all()
    )
    return [(event, summary_id, int(run_count or 0), int(volunteer_count or 0), state) for event, summary_id, run_count, volunteer_count, state in rows]


def backfill_platform(
    db: Session,
    platform_code: str,
    *,
    before_date: date,
    fetched_at: datetime,
    dry_run: bool,
    batch_size: int,
) -> dict[str, int]:
    platform = upsert.get_platform(db, platform_code)
    rows = _events_needing_backfill(db, platform, before_date=before_date)
    created = 0
    updated = 0

    for index, (event, summary_id, run_count, volunteer_count, state) in enumerate(rows, start=1):
        if state is None:
            state = ProtocolSyncState(
                event_id=event.id,
                event_summary_id=summary_id,
            )
            db.add(state)
            created += 1
        else:
            updated += 1
        state.last_protocol_fetched_at = fetched_at
        state.last_protocol_check_at = fetched_at
        state.run_results_count = run_count
        state.volunteer_results_count = volunteer_count
        state.protocol_source_hash = state.protocol_source_hash or "legacy_migration_backfill"

        if not dry_run and index % batch_size == 0:
            db.commit()

    if not dry_run:
        db.commit()

    return {
        "candidates": len(rows),
        "created": created,
        "updated": updated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill protocol_sync_states.last_protocol_fetched_at for migrated events",
    )
    parser.add_argument(
        "--before-date",
        default=DEFAULT_BEFORE_DATE.isoformat(),
        help="Mark events strictly before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--fetched-at",
        default=DEFAULT_FETCHED_AT.date().isoformat(),
        help="Value for last_protocol_fetched_at (YYYY-MM-DD, UTC midnight)",
    )
    parser.add_argument(
        "--platform",
        choices=[*SUPPORTED_PLATFORMS, "all"],
        default="all",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()

    before_date = date.fromisoformat(args.before_date)
    fetched_day = date.fromisoformat(args.fetched_at)
    fetched_at = datetime(fetched_day.year, fetched_day.month, fetched_day.day, tzinfo=timezone.utc)
    dry_run = not args.apply
    platforms = list(SUPPORTED_PLATFORMS) if args.platform == "all" else [args.platform]

    db = get_session_factory()()
    try:
        mode = "apply" if args.apply else "dry-run"
        print(
            f"backfill protocol_sync_states [{mode}]: "
            f"event_date < {before_date.isoformat()}, fetched_at={fetched_at.isoformat()}",
            flush=True,
        )
        total_candidates = 0
        for platform_code in platforms:
            stats = backfill_platform(
                db,
                platform_code,
                before_date=before_date,
                fetched_at=fetched_at,
                dry_run=dry_run,
                batch_size=args.batch_size,
            )
            total_candidates += stats["candidates"]
            print(
                f"{platform_code}: candidates={stats['candidates']} "
                f"created={stats['created']} updated={stats['updated']}",
                flush=True,
            )
        print(f"total_candidates={total_candidates}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
