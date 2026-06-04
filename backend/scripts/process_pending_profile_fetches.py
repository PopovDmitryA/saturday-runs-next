#!/usr/bin/env python3
"""Process profile_fetch_pending queue when platform cooldown blocks live fetches.

Requires Python 3.12+ (same as the backend). Recommended:

  docker compose exec api python scripts/process_pending_profile_fetches.py --clear-cooldown

  make process-pending-fetches ARGS="--clear-cooldown --platform parkrun"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    print(
        f"This script needs Python 3.12+, got {sys.version.split()[0]}.\n"
        "Run inside Docker instead:\n"
        "  docker compose exec api python scripts/process_pending_profile_fetches.py --clear-cooldown\n"
        "  make process-pending-fetches ARGS='--clear-cooldown'",
        file=sys.stderr,
    )
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.parkrun.fetch.captcha_state import clear_captcha_pending
from app.platform_fetch.cooldown import clear_all_platform_cooldowns, is_platform_in_cooldown
from app.services.profile_fetch_pending_service import list_pending_rows, process_pending_row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=("parkrun", "s95", "five_verst", "all"),
        default="all",
        help="Process only this platform (default: all)",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max pending rows per run")
    parser.add_argument(
        "--clear-cooldown",
        action="store_true",
        help="Clear Redis fetch cooldown keys before processing",
    )
    parser.add_argument("--dry-run", action="store_true", help="List pending rows without fetching")
    args = parser.parse_args()

    if args.clear_cooldown:
        cleared = clear_all_platform_cooldowns()
        clear_captcha_pending()
        print(f"Cleared {cleared} cooldown key(s) in Redis and captcha-pending flag")

    platform = None if args.platform == "all" else args.platform
    Session = get_session_factory()
    with Session() as db:
        rows = list_pending_rows(db, platform_code=platform, limit=args.limit)
        if not rows:
            print("No pending profile fetches.")
            return 0

        if args.dry_run:
            for row in rows:
                print(
                    f"{row.id} {row.platform_code} {row.external_user_id or row.profile_input} "
                    f"op={row.operation.value} attempts={row.attempts}"
                )
            return 0

        stats: dict[str, int] = {}
        for row in rows:
            if is_platform_in_cooldown(row.platform_code):
                stats["skipped_cooldown"] = stats.get("skipped_cooldown", 0) + 1
                print(f"skip cooldown: {row.platform_code} {row.external_user_id or row.profile_input}")
                continue
            outcome = process_pending_row(db, row)
            stats[outcome] = stats.get(outcome, 0) + 1
            label = row.external_user_id or row.profile_input
            if outcome == "cooldown":
                print(
                    f"cooldown: {row.platform_code} {label} — parkrun/сайт временно блокирует запросы, "
                    f"повторите через 15 мин (без --clear-cooldown)"
                )
            else:
                print(f"{outcome}: {row.platform_code} {label} (id={row.id})")

        print("Summary:", stats)
        if stats.get("cooldown"):
            print(
                "Подсказка: запросы из Docker часто попадают под защиту parkrun. "
                "Подождите cooldown или запустите скрипт с хоста (Python 3.12 + .env с localhost:5433)."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
