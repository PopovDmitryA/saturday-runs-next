#!/usr/bin/env python3
"""Sync parkrun runs for linked users via Chrome CDP (Mac). Use when profile is linked but runs are empty."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Platform, PlatformLink
from app.services.parkrun_local_worker import sync_parkrun_runs_for_user


def _resolve_user_ids(
    db,
    *,
    user_id: UUID | None,
    athlete_id: str | None,
    all_linked: bool,
) -> list[tuple[UUID, str]]:
    parkrun = db.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        return []

    query = (
        db.query(PlatformLink)
        .filter(PlatformLink.platform_id == parkrun.id)
    )
    if user_id is not None:
        query = query.filter(PlatformLink.user_id == user_id)
    if athlete_id:
        query = query.filter(PlatformLink.external_user_id == athlete_id.strip())
    links = query.all()
    if not links and not all_linked:
        return []

    if all_linked:
        links = (
            db.query(PlatformLink)
            .filter(PlatformLink.platform_id == parkrun.id)
            .all()
        )

    out: dict[UUID, str] = {}
    for link in links:
        out[link.user_id] = link.external_user_id
    return list(out.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync parkrun runs via Chrome CDP")
    parser.add_argument("--user-id", type=UUID, default=None, help="LK user UUID")
    parser.add_argument("--athlete-id", default=None, help="parkrun ID, e.g. 3197430")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync every user with a parkrun PlatformLink",
    )
    args = parser.parse_args()
    if not args.user_id and not args.athlete_id and not args.all:
        parser.error("Specify --user-id, --athlete-id, or --all")

    Session = get_session_factory()
    with Session() as db:
        targets = _resolve_user_ids(
            db,
            user_id=args.user_id,
            athlete_id=args.athlete_id,
            all_linked=args.all,
        )
        if not targets:
            print("No parkrun PlatformLink found.", file=sys.stderr)
            return 1

        failed = 0
        for user_id, label in targets:
            line = sync_parkrun_runs_for_user(db, user_id, label=label)
            print(line)
            if not line.startswith("sync_ok"):
                failed += 1
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
