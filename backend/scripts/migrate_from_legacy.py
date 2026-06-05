#!/usr/bin/env python3
"""One-time ETL from legacy five_verst_stats into saturday_runs_lk."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.migration import five_verst, parkrun, s95_locations, validate
from app.migration.legacy_db import get_legacy_engine, legacy_connection
from app.migration.stats import MigrationStats


def _parse_steps(raw: str | None, default: set[str]) -> set[str]:
    if not raw:
        return default
    return {part.strip() for part in raw.split(",") if part.strip()}


def _print_stats(label: str, stats: MigrationStats) -> None:
    print(f"\n=== {label} ===", flush=True)
    payload = stats.as_dict()
    for key, value in payload.items():
        if key == "errors":
            continue
        print(f"  {key}: {value}", flush=True)
    if stats.errors:
        print(f"  errors ({len(stats.errors)} shown):", flush=True)
        for err in stats.errors[:20]:
            print(f"    · {err}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate historical data from legacy five_verst_stats")
    parser.add_argument(
        "--platform",
        choices=("five_verst", "parkrun", "s95", "all"),
        default="five_verst",
        help="Which platform chunk to migrate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read legacy and map rows, but do not write to saturday_runs_lk",
    )
    parser.add_argument("--batch-size", type=int, default=5000, help="Commit batch size for runs/vols")
    parser.add_argument("--limit", type=int, default=None, help="Limit rows (for local smoke tests)")
    parser.add_argument(
        "--steps",
        default=None,
        help="Comma-separated sub-steps. "
        "five_verst: locations,events,runs,volunteers. "
        "parkrun: participants,runs,vol_summary. "
        "s95: locations",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Compare legacy vs target counts and exit",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    Session = get_session_factory()

    if args.validate:
        with Session() as db:
            if args.platform == "all":
                result = validate.validate_all(db)
            else:
                result = {args.platform: validate.validate_platform(db, args.platform)}
        indent = 2 if args.pretty else None
        print(json.dumps(result, ensure_ascii=False, indent=indent))
        return 0

    engine = get_legacy_engine()
    stats = MigrationStats()
    try:
        with legacy_connection(engine) as conn, Session() as db:
            if args.platform in ("five_verst", "all"):
                steps = _parse_steps(
                    args.steps if args.platform != "all" else None,
                    {"locations", "events", "runs", "volunteers"},
                )
                fv_stats = five_verst.migrate_all(
                    db,
                    conn,
                    dry_run=args.dry_run,
                    batch_size=args.batch_size,
                    limit=args.limit,
                    steps=steps,
                )
                stats.merge(fv_stats)
                _print_stats("five_verst", fv_stats)

            if args.platform in ("parkrun", "all"):
                steps = _parse_steps(
                    args.steps if args.platform != "all" else None,
                    {"participants", "runs", "vol_summary"},
                )
                pr_stats = parkrun.migrate_all(
                    db,
                    conn,
                    dry_run=args.dry_run,
                    batch_size=args.batch_size,
                    limit=args.limit,
                    steps=steps,
                )
                stats.merge(pr_stats)
                _print_stats("parkrun", pr_stats)

            if args.platform in ("s95", "all"):
                steps = _parse_steps(args.steps if args.platform != "all" else None, {"locations"})
                if "locations" in steps:
                    s95_stats = s95_locations.migrate_locations(db, conn, dry_run=args.dry_run)
                    stats.merge(s95_stats)
                    _print_stats("s95 locations", s95_stats)

            if args.dry_run:
                db.rollback()
                print("\n(dry-run: no changes committed)", flush=True)
    finally:
        engine.dispose()

    if args.platform == "all":
        _print_stats("total", stats)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
