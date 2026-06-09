#!/usr/bin/env python3
"""Restore position and age_category on run_results from legacy protocol tables."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.migration.helpers import (
    five_verst_run_key,
    five_verst_unknown_user_id,
    is_five_verst_unknown_runner,
    is_s95_unknown_runner,
    legacy_timestamp_to_date,
    s95_run_key,
    s95_unknown_user_id,
)
from app.migration.legacy_db import legacy_connection, legacy_rows
from app.migration.lookups import FiveVerstLookups, ParkrunLookups, S95Lookups
from app.models import Event, Location, Platform, PlatformLink, RunResult, User
from app.services.dashboard_service import recompute_dashboard_cache

DEFAULT_USER_NAMES = (
    "Попов Дмитрий",
    "Nataliya",
    "Юлия Шутихина",
)


@dataclass
class BackfillStats:
    legacy_rows: int = 0
    matched: int = 0
    updated: int = 0
    skipped_no_slug: int = 0
    skipped_no_match: int = 0
    errors: list[str] = field(default_factory=list)


def _resolve_users(db: Session, names: list[str]) -> list[User]:
    users: list[User] = []
    for name in names:
        row = (
            db.query(User)
            .filter(User.display_name.ilike(f"%{name.strip()}%"))
            .order_by(User.created_at.asc())
            .first()
        )
        if row is None:
            raise SystemExit(f"User not found for name pattern: {name!r}")
        users.append(row)
    return users


def _age_category_from_row(row: dict, platform_code: str) -> str | None:
    if platform_code == "parkrun":
        raw = row.get("age_grade")
    else:
        raw = row.get("age_category")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _position_from_row(row: dict) -> int | None:
    raw = row.get("position")
    if raw is None:
        return None
    return int(raw)


def _five_verst_user_id(row: dict, slug: str, event_date) -> str | None:
    user_id_raw = str(row["user_id"]).strip() if row.get("user_id") else ""
    name_runner = str(row["name_runner"]).strip() if row.get("name_runner") else None
    status_runner = str(row["status_runner"]).strip() if row.get("status_runner") else None
    if is_five_verst_unknown_runner(
        user_id=user_id_raw or None,
        name_runner=name_runner,
        status_runner=status_runner,
    ):
        position = _position_from_row(row)
        if position is None:
            return None
        return five_verst_unknown_user_id(slug, event_date, position)
    if not user_id_raw:
        return None
    return user_id_raw


def _five_verst_match(row: dict, lookups: FiveVerstLookups) -> tuple[list[str], str, str] | None:
    name = str(row["name_point"]).strip()
    event_date = legacy_timestamp_to_date(row["date_event"])
    if event_date is None:
        return None
    slug = lookups.resolve_slug(name)
    if not slug:
        return None
    user_id = _five_verst_user_id(row, slug, event_date)
    if user_id is None:
        return None
    date_iso = event_date.isoformat()
    return (
        [
            five_verst_run_key(slug, event_date, user_id),
            f"{user_id}:{date_iso}:{slug}",
        ],
        date_iso,
        slug,
    )


def _s95_user_id(row: dict, slug: str, event_date) -> str | None:
    user_id_raw = str(row["user_id"]).strip() if row.get("user_id") else ""
    status_runner = str(row["status_runner"]).strip() if row.get("status_runner") else None
    position = _position_from_row(row)
    if is_s95_unknown_runner(user_id=user_id_raw or None, status_runner=status_runner):
        if position is None:
            return None
        return s95_unknown_user_id(slug, event_date, position)
    if not user_id_raw:
        return None
    return user_id_raw


def _s95_match(row: dict, lookups: S95Lookups) -> tuple[list[str], str, str] | None:
    name = str(row["name_point"]).strip()
    event_date = legacy_timestamp_to_date(row["date_event"])
    if event_date is None:
        return None
    slug = lookups.resolve_slug(name)
    if not slug:
        return None
    user_id = _s95_user_id(row, slug, event_date)
    if user_id is None:
        return None
    position = _position_from_row(row)
    date_iso = event_date.isoformat()
    return (
        [
            s95_run_key(slug, event_date, user_id, position),
            f"profile:{user_id}:{date_iso}:{slug}",
        ],
        date_iso,
        slug,
    )


def _parkrun_match(row: dict, lookups: ParkrunLookups) -> tuple[list[str], str, str] | None:
    name_point = str(row["name_point"]).strip()
    event_date = legacy_timestamp_to_date(row["date_event"])
    if event_date is None:
        return None
    user_id = str(row["user_id"]).strip()
    slug = lookups.resolve_slug(name_point)
    run_number_raw = row.get("index_event")
    run_number = int(run_number_raw) if run_number_raw is not None else 0
    date_iso = event_date.isoformat()
    key = f"parkrun:{user_id}:{slug}:{date_iso}:{run_number}"
    return ([key], date_iso, slug)


def _legacy_match(
    row: dict,
    platform_code: str,
    *,
    five_verst: FiveVerstLookups,
    s95: S95Lookups,
    parkrun: ParkrunLookups,
) -> tuple[list[str], str, str] | None:
    if platform_code == "five_verst":
        return _five_verst_match(row, five_verst)
    if platform_code == "s95":
        return _s95_match(row, s95)
    if platform_code == "parkrun":
        return _parkrun_match(row, parkrun)
    return None


def _load_platform_lookups(db: Session, conn) -> tuple[FiveVerstLookups, S95Lookups, ParkrunLookups]:
    five_verst = FiveVerstLookups()
    s95 = S95Lookups()
    parkrun = ParkrunLookups()
    try:
        five_verst.load_from_legacy(conn)
    except Exception:
        pass
    try:
        s95.load_from_legacy(conn)
    except Exception:
        pass
    parkrun.load_users_from_legacy(conn)
    for platform_code, lookups in (("five_verst", five_verst), ("s95", s95)):
        platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
        if platform is None:
            continue
        for location in db.query(Location).filter(Location.platform_id == platform.id):
            lookups.register_name_slug(location.name, location.external_key)
    return five_verst, s95, parkrun


def _find_run(
    *,
    candidate_keys: list[str],
    date_iso: str,
    slug: str,
    existing_by_key: dict[str, RunResult],
    by_date_slug: dict[tuple[str, str], RunResult],
) -> RunResult | None:
    for key in candidate_keys:
        run = existing_by_key.get(key)
        if run is not None:
            return run
    return by_date_slug.get((date_iso, slug))


def _legacy_rows_for_user(conn, platform_code: str, external_user_id: str) -> list[dict]:
    if platform_code == "five_verst":
        sql = """
            SELECT name_point, date_event, user_id, name_runner, position, age_category, status_runner
            FROM details_protocol
            WHERE user_id = :user_id
              AND name_point IS NOT NULL
              AND date_event IS NOT NULL
            ORDER BY date_event DESC
        """
    elif platform_code == "s95":
        sql = """
            SELECT name_point, date_event, user_id, name_runner, position, status_runner
            FROM s95_details_protocol
            WHERE user_id = :user_id
              AND name_point IS NOT NULL
              AND date_event IS NOT NULL
            ORDER BY date_event DESC
        """
    elif platform_code == "parkrun":
        sql = """
            SELECT user_id, name_point, date_event, index_event, position, age_grade
            FROM parkrun_details_protocol
            WHERE user_id = :user_id
              AND name_point IS NOT NULL
              AND date_event IS NOT NULL
            ORDER BY date_event DESC
        """
    else:
        return []
    return [dict(row) for row in legacy_rows(conn, sql, {"user_id": external_user_id})]


def _key_for_row(
    row: dict,
    platform_code: str,
    *,
    five_verst: FiveVerstLookups,
    s95: S95Lookups,
    parkrun: ParkrunLookups,
) -> tuple[list[str], str, str] | None:
    return _legacy_match(row, platform_code, five_verst=five_verst, s95=s95, parkrun=parkrun)


def backfill_user(
    db: Session,
    conn,
    user: User,
    *,
    five_verst: FiveVerstLookups,
    s95: S95Lookups,
    parkrun: ParkrunLookups,
    dry_run: bool,
) -> BackfillStats:
    stats = BackfillStats()
    links = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id, PlatformLink.participant_id.isnot(None))
        .all()
    )

    for link, platform in links:
        external_user_id = link.external_user_id.strip()
        participant_id = link.participant_id
        if not external_user_id or participant_id is None:
            continue

        runs_with_ctx = (
            db.query(RunResult, Event, Location)
            .join(Event, RunResult.event_id == Event.id)
            .join(Location, Event.location_id == Location.id)
            .filter(RunResult.participant_id == participant_id)
            .all()
        )
        existing_by_key = {run.external_result_key: run for run, _, _ in runs_with_ctx}
        by_date_slug = {
            (event.event_date.isoformat(), location.external_key): run
            for run, event, location in runs_with_ctx
        }

        for row in _legacy_rows_for_user(conn, platform.code, external_user_id):
            stats.legacy_rows += 1
            match = _key_for_row(
                row,
                platform.code,
                five_verst=five_verst,
                s95=s95,
                parkrun=parkrun,
            )
            if match is None:
                stats.skipped_no_slug += 1
                continue

            candidate_keys, date_iso, slug = match
            run = _find_run(
                candidate_keys=candidate_keys,
                date_iso=date_iso,
                slug=slug,
                existing_by_key=existing_by_key,
                by_date_slug=by_date_slug,
            )
            if run is None:
                stats.skipped_no_match += 1
                continue

            stats.matched += 1
            position = _position_from_row(row)
            age_category = _age_category_from_row(row, platform.code)
            changed = False
            if position is not None and run.position != position:
                changed = True
                if not dry_run:
                    run.position = position
            if age_category is not None and run.age_category != age_category:
                changed = True
                if not dry_run:
                    run.age_category = age_category
            if changed:
                stats.updated += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill run position/age_category from legacy DB")
    parser.add_argument(
        "--user-name",
        action="append",
        dest="user_names",
        help="Match users.display_name (substring). Default: three prod users",
    )
    parser.add_argument("--user-id", action="append", dest="user_ids", type=UUID, help="Explicit user UUID")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument(
        "--recompute-dashboard",
        action="store_true",
        help="Recompute dashboard cache after backfill",
    )
    args = parser.parse_args()

    Session = get_session_factory()
    with Session() as db:
        with legacy_connection() as conn:
            five_verst, s95, parkrun = _load_platform_lookups(db, conn)

            users: list[User] = []
            if args.user_ids:
                for user_id in args.user_ids:
                    user = db.query(User).filter(User.id == user_id).one_or_none()
                    if user is None:
                        raise SystemExit(f"User not found: {user_id}")
                    users.append(user)
            else:
                names = args.user_names or list(DEFAULT_USER_NAMES)
                users = _resolve_users(db, names)

            total = BackfillStats()
            for user in users:
                stats = backfill_user(
                    db,
                    conn,
                    user,
                    five_verst=five_verst,
                    s95=s95,
                    parkrun=parkrun,
                    dry_run=args.dry_run,
                )
                print(
                    f"{user.display_name} ({user.id}): "
                    f"legacy={stats.legacy_rows} matched={stats.matched} updated={stats.updated} "
                    f"no_slug={stats.skipped_no_slug} no_match={stats.skipped_no_match}",
                    flush=True,
                )
                total.legacy_rows += stats.legacy_rows
                total.matched += stats.matched
                total.updated += stats.updated
                total.skipped_no_slug += stats.skipped_no_slug
                total.skipped_no_match += stats.skipped_no_match
                if args.recompute_dashboard and not args.dry_run:
                    recompute_dashboard_cache(db, user.id)

            if not args.dry_run:
                db.commit()
            else:
                db.rollback()

            print(
                f"TOTAL: legacy={total.legacy_rows} matched={total.matched} updated={total.updated} "
                f"no_slug={total.skipped_no_slug} no_match={total.skipped_no_match}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
