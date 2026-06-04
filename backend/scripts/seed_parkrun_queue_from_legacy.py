#!/usr/bin/env python3
"""Put 5 parkrun accounts from legacy DB into profile_fetch_pending for make parkrun."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import ProfileFetchPendingOperation
from app.services.parkrun_legacy_seed import (
    fetch_parkrun_accounts_from_legacy,
    seed_parkrun_queue_from_legacy,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed parkrun queue from legacy five_verst_stats")
    parser.add_argument("--limit", type=int, default=5, help="Number of parkrun accounts")
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Queue profile_preview instead of activity_import",
    )
    parser.add_argument(
        "--source",
        choices=("tg_bindings", "most_active"),
        default="tg_bindings",
        help="tg_bindings = tg_user_profile.parkrun_user_id (fast); most_active = last run in protocol",
    )
    args = parser.parse_args()

    accounts = fetch_parkrun_accounts_from_legacy(limit=args.limit, source=args.source)
    print(f"Legacy: выбрано {len(accounts)} профилей parkrun:", flush=True)
    for account in accounts:
        name = account.display_name or "—"
        print(f"  · {account.athlete_id} — {name}", flush=True)

    operation = (
        ProfileFetchPendingOperation.profile_preview
        if args.preview_only
        else ProfileFetchPendingOperation.activity_import
    )
    Session = get_session_factory()
    with Session() as db:
        labels = seed_parkrun_queue_from_legacy(db, accounts, operation=operation)

    print(f"\nВ очереди LK (pending): {len(labels)}", flush=True)
    for label in labels:
        print(f"  ✓ {label}", flush=True)
    print("\nЗапустите: make parkrun", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
