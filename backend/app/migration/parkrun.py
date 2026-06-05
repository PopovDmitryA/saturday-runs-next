from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.migration.helpers import (
    legacy_time_display,
    legacy_time_to_seconds,
    legacy_timestamp_to_date,
)
from app.migration.legacy_db import legacy_row_stream, legacy_rows
from app.migration.lookups import ParkrunLookups, TargetLookups
from app.migration.stats import MigrationStats
from app.platform_adapters.canonical import (
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.parkrun.parsers.athlete import _slugify_event
from app.sync import upsert

PLATFORM_CODE = "parkrun"
SYNTHETIC_VOL_DATE = date(1970, 1, 1)


def _ensure_summary_location(db: Session, platform, target: TargetLookups, *, dry_run: bool) -> None:
    if dry_run:
        return
    location, _ = upsert.upsert_location(
        db,
        platform,
        CanonicalLocation(
            external_key=ParkrunLookups.PARKRUN_SUMMARY_SLUG,
            name=ParkrunLookups.PARKRUN_SUMMARY_NAME,
            country="United Kingdom",
            source_url="https://www.parkrun.org.uk/",
        ),
    )
    target.remember_location(location)


def migrate_participants(
    db: Session,
    conn,
    *,
    dry_run: bool,
    limit: int | None = None,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)

    sql = """
        SELECT
            user_id,
            actual_name_runner,
            name_runner,
            actual_age_category,
            last_updated
        FROM parkrun_users
        WHERE user_id IS NOT NULL
          AND TRIM(user_id::text) <> ''
        ORDER BY user_id
    """
    params: dict[str, object] = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    for row in legacy_row_stream(conn, sql, params):
        user_id = str(row["user_id"]).strip()
        display_name = (
            str(row["actual_name_runner"]).strip()
            if row.get("actual_name_runner")
            else (str(row["name_runner"]).strip() if row.get("name_runner") else f"A{user_id}")
        )
        if dry_run:
            stats.participants += 1
            continue
        upsert.upsert_participant(
            db,
            platform,
            external_user_id=user_id,
            display_name=display_name,
            profile_url=f"https://www.parkrun.org.uk/parkrunner/{user_id}/",
            age_category=(str(row["actual_age_category"]).strip() if row.get("actual_age_category") else None),
        )
        stats.participants += 1

    if not dry_run:
        db.commit()
    return stats


def migrate_runs(
    db: Session,
    conn,
    parkrun_lookups: ParkrunLookups,
    *,
    dry_run: bool,
    batch_size: int = 2000,
    limit: int | None = None,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)

    sql = """
        SELECT
            user_id,
            name_point,
            date_event,
            index_event,
            position,
            finish_time,
            age_grade,
            pr
        FROM parkrun_details_protocol
        WHERE user_id IS NOT NULL
          AND name_point IS NOT NULL
          AND date_event IS NOT NULL
          AND TRIM(user_id::text) <> ''
        ORDER BY date_event, name_point, position
    """
    params: dict[str, object] = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    batch: list[CanonicalRunResult] = []
    for row in legacy_row_stream(conn, sql, params):
        user_id = str(row["user_id"]).strip()
        name_point = str(row["name_point"]).strip()
        event_date = legacy_timestamp_to_date(row["date_event"])
        if event_date is None:
            stats.skipped += 1
            continue

        slug = parkrun_lookups.resolve_slug(name_point)
        run_number_raw = row.get("index_event")
        run_number = int(run_number_raw) if run_number_raw is not None else 0
        finish_sec = legacy_time_to_seconds(row.get("finish_time"))
        position_raw = row.get("position")
        position = int(position_raw) if position_raw is not None else None
        pr_raw = str(row.get("pr") or "").strip()
        is_pr = bool(pr_raw and pr_raw not in {"-", "—"})

        batch.append(
            CanonicalRunResult(
                external_result_key=f"parkrun:{user_id}:{slug}:{event_date.isoformat()}:{run_number}",
                event_date=event_date,
                external_user_id=user_id,
                participant_name=parkrun_lookups.participant_name(user_id),
                position=position,
                finish_time_sec=finish_sec,
                finish_time_display=legacy_time_display(row.get("finish_time"), finish_sec),
                age_category=(str(row["age_grade"]).strip() if row.get("age_grade") else None),
                is_pr=is_pr,
                location_external_key=slug,
                location_name=name_point,
                event_number=run_number or None,
            )
        )

        if len(batch) >= batch_size:
            if dry_run:
                stats.runs += len(batch)
            else:
                stats.runs += upsert.import_profile_run_results(db, platform, batch)
                db.commit()
            batch = []

    if batch:
        if dry_run:
            stats.runs += len(batch)
        else:
            stats.runs += upsert.import_profile_run_results(db, platform, batch)
            db.commit()
    return stats


def migrate_volunteer_summary(
    db: Session,
    conn,
    parkrun_lookups: ParkrunLookups,
    *,
    dry_run: bool,
    batch_size: int = 2000,
    limit: int | None = None,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    target = TargetLookups(db, platform)
    _ensure_summary_location(db, platform, target, dry_run=dry_run)

    sql = """
        SELECT user_id, vol_role, count_vol
        FROM parkrun_vol_summary
        WHERE user_id IS NOT NULL
          AND vol_role IS NOT NULL
          AND TRIM(user_id::text) <> ''
        ORDER BY user_id, vol_role
    """
    params: dict[str, object] = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    batch: list[CanonicalVolunteerResult] = []
    participant_cache: dict[str, object] = {}

    for row in legacy_row_stream(conn, sql, params):
        user_id = str(row["user_id"]).strip()
        role = str(row["vol_role"]).strip()
        count = int(row["count_vol"]) if row.get("count_vol") is not None else 0
        if not role or count <= 0:
            stats.skipped += 1
            continue

        role_slug = _slugify_event(role)
        batch.append(
            CanonicalVolunteerResult(
                external_result_key=f"parkrun:{user_id}:vol-summary:{role_slug}",
                event_date=SYNTHETIC_VOL_DATE,
                external_user_id=user_id,
                participant_name=parkrun_lookups.participant_name(user_id),
                role=f"{role} ({count}×)",
                location_external_key=ParkrunLookups.PARKRUN_SUMMARY_SLUG,
                location_name=ParkrunLookups.PARKRUN_SUMMARY_NAME,
            )
        )

        if len(batch) >= batch_size:
            if dry_run:
                stats.volunteers += len(batch)
            else:
                stats.volunteers += _flush_volunteer_batch(db, platform, batch, participant_cache)
                db.commit()
            batch = []

    if batch:
        if dry_run:
            stats.volunteers += len(batch)
        else:
            stats.volunteers += _flush_volunteer_batch(db, platform, batch, participant_cache)
            db.commit()
    return stats


def _flush_volunteer_batch(
    db: Session,
    platform,
    batch: list[CanonicalVolunteerResult],
    participant_cache: dict[str, object],
) -> int:
    imported = 0
    by_user: dict[str, list[CanonicalVolunteerResult]] = {}
    for item in batch:
        user_id = item.external_user_id or ""
        by_user.setdefault(user_id, []).append(item)

    for user_id, items in by_user.items():
        participant = participant_cache.get(user_id)
        if participant is None:
            participant = upsert.upsert_participant(
                db,
                platform,
                external_user_id=user_id,
                display_name=items[0].participant_name or f"A{user_id}",
                profile_url=f"https://www.parkrun.org.uk/parkrunner/{user_id}/",
            )
            participant_cache[user_id] = participant
        imported += upsert.import_profile_volunteer_results(db, platform, participant.id, items)
    return imported


def migrate_all(
    db: Session,
    conn,
    *,
    dry_run: bool,
    batch_size: int,
    limit: int | None,
    steps: set[str] | None = None,
) -> MigrationStats:
    steps = steps or {"participants", "runs", "vol_summary"}
    stats = MigrationStats()
    parkrun_lookups = ParkrunLookups()
    parkrun_lookups.load_users_from_legacy(conn)

    if "participants" in steps:
        migrate_participants(db, conn, dry_run=dry_run, limit=limit, stats=stats)
    if "runs" in steps:
        migrate_runs(
            db,
            conn,
            parkrun_lookups,
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            stats=stats,
        )
    if "vol_summary" in steps:
        migrate_volunteer_summary(
            db,
            conn,
            parkrun_lookups,
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            stats=stats,
        )
    return stats
