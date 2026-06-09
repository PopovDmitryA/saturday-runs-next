from __future__ import annotations

import hashlib
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.migration.helpers import (
    is_s95_unknown_runner,
    legacy_time_display,
    legacy_time_to_seconds,
    legacy_timestamp_to_date,
    s95_country_from_url,
    s95_event_key,
    s95_run_key,
    s95_unknown_user_id,
    s95_volunteer_key,
    slug_from_s95_url,
)
from app.migration.legacy_db import legacy_row_stream, legacy_rows
from app.migration.lookups import S95Lookups, TargetLookups
from app.migration.s95_locations import migrate_locations as migrate_s95_locations
from app.migration.stats import MigrationStats
from app.models import Event
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.sync import upsert

PLATFORM_CODE = "s95"


def _summary_hash(external_event_key: str, finishers: int | None, volunteers: int | None) -> str:
    return hashlib.sha256(f"{external_event_key}:{finishers}:{volunteers}".encode()).hexdigest()[:32]


def migrate_events(
    db: Session,
    conn,
    lookups: S95Lookups,
    *,
    dry_run: bool,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    target = TargetLookups(db, platform)
    if not dry_run:
        target.preload_locations()

    for row in legacy_rows(
        conn,
        """
        SELECT
            name_point,
            date_event,
            index_event,
            count_runners,
            count_vol,
            best_time_man,
            best_time_woman,
            link_event
        FROM s95_list_all_events
        WHERE name_point IS NOT NULL AND date_event IS NOT NULL
        ORDER BY date_event, name_point
        """,
    ):
        name = str(row["name_point"]).strip()
        slug = lookups.resolve_slug(name)
        if not slug and row.get("link_event"):
            slug = slug_from_s95_url(str(row["link_event"]))
            if slug:
                lookups.register_name_slug(name, slug)
        if not slug:
            stats.skipped += 1
            stats.add_error(f"s95 event: unknown location name {name!r}")
            continue

        event_date = legacy_timestamp_to_date(row["date_event"])
        if event_date is None:
            stats.skipped += 1
            continue

        index_event = row.get("index_event")
        event_number = int(index_event) if index_event is not None else None
        external_event_key = s95_event_key(slug, event_date)
        finishers = int(row["count_runners"]) if row.get("count_runners") is not None else None
        volunteers = int(row["count_vol"]) if row.get("count_vol") is not None else None
        best_male_sec = legacy_time_to_seconds(row.get("best_time_man"))
        best_female_sec = legacy_time_to_seconds(row.get("best_time_woman"))
        source_url = str(row["link_event"]).strip() if row.get("link_event") else ""
        summary = CanonicalEventSummary(
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=event_number,
            location_external_key=slug,
            location_name=lookups.name_by_slug.get(slug, name),
            finishers_count=finishers,
            volunteers_count=volunteers,
            best_male_time_sec=best_male_sec,
            best_male_time_display=legacy_time_display(row.get("best_time_man"), best_male_sec),
            best_female_time_sec=best_female_sec,
            best_female_time_display=legacy_time_display(row.get("best_time_woman"), best_female_sec),
            is_test_event=False,
            source_url=source_url,
            summary_hash=_summary_hash(external_event_key, finishers, volunteers),
        )

        if dry_run:
            stats.event_summaries += 1
            stats.events += 1
            continue

        location = target.get_location(slug)
        if location is None:
            location, _ = upsert.upsert_location(
                db,
                platform,
                CanonicalLocation(
                    external_key=slug,
                    name=summary.location_name,
                    country=s95_country_from_url(source_url),
                    source_url=source_url or f"https://s95.ru/events/{slug}/",
                ),
            )
            target.remember_location(location)

        summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
        event_row = upsert.upsert_event_for_summary(db, platform, location, summary, summary_row)
        target.remember_event(slug, event_row)
        stats.event_summaries += 1
        stats.events += 1

    if not dry_run:
        db.flush()
        target.preload_events()
    return stats


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
            s95_id,
            name_runner,
            s95_barcode,
            link_s95_runner,
            planning
        FROM s95_runners
        WHERE s95_id IS NOT NULL
          AND TRIM(s95_id) <> ''
        ORDER BY s95_id
    """
    params: dict[str, object] = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    for row in legacy_row_stream(conn, sql, params):
        user_id = str(row["s95_id"]).strip()
        display_name = str(row["name_runner"]).strip() if row.get("name_runner") else f"S95 {user_id}"
        profile_url = str(row["link_s95_runner"]).strip() if row.get("link_s95_runner") else ""
        if not profile_url:
            profile_url = f"https://s95.ru/athletes/{user_id}/"
        if dry_run:
            stats.participants += 1
            continue
        participant = upsert.upsert_participant(
            db,
            platform,
            external_user_id=user_id,
            display_name=display_name,
            profile_url=profile_url,
        )
        barcode = str(row["s95_barcode"]).strip() if row.get("s95_barcode") else None
        planning = str(row["planning"]).strip() if row.get("planning") else None
        if barcode:
            participant.barcode_id = barcode
        if planning:
            participant.planning_location = planning
        stats.participants += 1

    if not dry_run:
        db.commit()
    return stats


def _ensure_event(
    db: Session,
    platform,
    target: TargetLookups,
    lookups: S95Lookups,
    *,
    name_point: str,
    event_date: date,
    dry_run: bool,
) -> UUID | None:
    slug = lookups.resolve_slug(name_point)
    if not slug:
        return None

    cached = target.get_event(slug, event_date)
    if cached is not None:
        return cached.id

    meta = lookups.event_meta_by_name_date.get((name_point.strip(), event_date.isoformat()))
    event_number = meta.event_number if meta else None
    source_url = meta.source_url if meta and meta.source_url else (
        f"https://s95.ru/events/{slug}/results/{event_date.strftime('%d.%m.%Y')}/"
    )
    location_name = lookups.name_by_slug.get(slug, name_point.strip())

    if dry_run:
        return UUID(int=0)

    location = target.get_location(slug)
    if location is None:
        location, _ = upsert.upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=slug,
                name=location_name,
                country=s95_country_from_url(source_url),
                source_url=f"https://s95.ru/events/{slug}/",
            ),
        )
        target.remember_location(location)

    external_event_key = s95_event_key(slug, event_date)
    event = upsert.upsert_event_for_profile(
        db,
        platform,
        location,
        external_event_key=external_event_key,
        event_date=event_date,
        event_number=event_number,
        location_name=location_name,
        location_slug=slug,
        source_url=source_url,
        is_test_event=False,
    )
    target.remember_event(slug, event)
    return event.id


def migrate_runs(
    db: Session,
    conn,
    lookups: S95Lookups,
    *,
    dry_run: bool,
    batch_size: int = 5000,
    limit: int | None = None,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    target = TargetLookups(db, platform)
    if not dry_run:
        target.preload_locations()
        target.preload_events()

    sql = """
        SELECT
            name_point,
            date_event,
            user_id,
            name_runner,
            link_runner,
            position,
            finish_time,
            pace,
            club_name,
            status_runner
        FROM s95_details_protocol
        WHERE name_point IS NOT NULL
          AND date_event IS NOT NULL
          AND (
            (user_id IS NOT NULL AND TRIM(user_id) <> '')
            OR status_runner = 'unknown_runner'
          )
        ORDER BY date_event, name_point, position
    """
    params: dict[str, object] = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    batch: list[CanonicalRunResult] = []
    batch_event_id: UUID | None = None
    batch_slug: str | None = None
    batch_date: date | None = None

    def flush_batch() -> None:
        nonlocal batch, batch_event_id, stats
        if not batch:
            return
        if dry_run:
            stats.runs += len(batch)
            batch = []
            return
        if batch_event_id is None:
            stats.skipped += len(batch)
            batch = []
            return
        event = db.query(Event).filter_by(id=batch_event_id).one()
        stats.runs += upsert.upsert_run_results(db, event, platform, batch)
        batch = []

    for row in legacy_row_stream(conn, sql, params):
        name = str(row["name_point"]).strip()
        event_date = legacy_timestamp_to_date(row["date_event"])
        if event_date is None:
            stats.skipped += 1
            continue

        slug = lookups.resolve_slug(name)
        if not slug:
            stats.skipped += 1
            stats.add_error(f"s95 run: unknown location {name!r}")
            continue

        position_raw = row.get("position")
        position = int(position_raw) if position_raw is not None else None
        status_runner = str(row["status_runner"]).strip() if row.get("status_runner") else None
        user_id_raw = str(row["user_id"]).strip() if row.get("user_id") else ""
        is_unknown = is_s95_unknown_runner(user_id=user_id_raw or None, status_runner=status_runner)
        if not user_id_raw and not is_unknown:
            stats.skipped += 1
            continue
        if is_unknown:
            if position is None:
                stats.skipped += 1
                continue
            user_id = s95_unknown_user_id(slug, event_date, position)
            participant_name = (
                str(row["name_runner"]).strip() if row.get("name_runner") else "НЕИЗВЕСТНЫЙ"
            )
            run_status = status_runner or "unknown_runner"
        else:
            user_id = user_id_raw
            participant_name = (
                str(row["name_runner"]).strip() if row.get("name_runner") else f"S95 {user_id}"
            )
            run_status = status_runner

        finish_sec = legacy_time_to_seconds(row.get("finish_time"))
        pace_sec = legacy_time_to_seconds(row.get("pace"))
        meta = lookups.event_meta_by_name_date.get((name, event_date.isoformat()))
        event_number = meta.event_number if meta else None
        canonical = CanonicalRunResult(
            external_result_key=s95_run_key(slug, event_date, user_id, position),
            event_date=event_date,
            external_user_id=user_id,
            participant_name=participant_name,
            position=position,
            finish_time_sec=finish_sec,
            finish_time_display=legacy_time_display(row.get("finish_time"), finish_sec),
            pace_sec_per_km=pace_sec,
            pace_display=legacy_time_display(row.get("pace"), pace_sec),
            status=run_status,
            club_name=(str(row["club_name"]).strip() if row.get("club_name") else None),
            location_external_key=slug,
            location_name=lookups.name_by_slug.get(slug, name),
            event_number=event_number,
        )

        if batch_date != event_date or batch_slug != slug:
            flush_batch()
            if not dry_run:
                db.commit()
            batch_event_id = _ensure_event(
                db,
                platform,
                target,
                lookups,
                name_point=name,
                event_date=event_date,
                dry_run=dry_run,
            )
            batch_date = event_date
            batch_slug = slug

        batch.append(canonical)
        if len(batch) >= batch_size:
            flush_batch()
            if not dry_run:
                db.commit()

    flush_batch()
    if not dry_run:
        db.commit()
    return stats


def migrate_volunteers(
    db: Session,
    conn,
    lookups: S95Lookups,
    *,
    dry_run: bool,
    batch_size: int = 5000,
    limit: int | None = None,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    target = TargetLookups(db, platform)
    if not dry_run:
        target.preload_locations()
        target.preload_events()

    sql = """
        SELECT
            name_point,
            date_event,
            user_id,
            name_runner,
            vol_role
        FROM s95_details_vol
        WHERE name_point IS NOT NULL
          AND date_event IS NOT NULL
          AND user_id IS NOT NULL
          AND TRIM(user_id) <> ''
          AND vol_role IS NOT NULL
          AND TRIM(vol_role) <> ''
        ORDER BY date_event, name_point
    """
    params: dict[str, object] = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit

    batch: list[CanonicalVolunteerResult] = []
    batch_event_id: UUID | None = None
    batch_slug: str | None = None
    batch_date: date | None = None

    def flush_batch() -> None:
        nonlocal batch, batch_event_id, stats
        if not batch:
            return
        if dry_run:
            stats.volunteers += len(batch)
            batch = []
            return
        if batch_event_id is None:
            stats.skipped += len(batch)
            batch = []
            return
        event = db.query(Event).filter_by(id=batch_event_id).one()
        stats.volunteers += upsert.upsert_volunteer_results(db, event, platform, batch)
        batch = []

    for row in legacy_row_stream(conn, sql, params):
        name = str(row["name_point"]).strip()
        event_date = legacy_timestamp_to_date(row["date_event"])
        if event_date is None:
            stats.skipped += 1
            continue

        slug = lookups.resolve_slug(name)
        if not slug:
            stats.skipped += 1
            stats.add_error(f"s95 volunteer: unknown location {name!r}")
            continue

        user_id = str(row["user_id"]).strip()
        role = str(row["vol_role"]).strip()
        participant_name = (
            str(row["name_runner"]).strip() if row.get("name_runner") else f"S95 {user_id}"
        )
        meta = lookups.event_meta_by_name_date.get((name, event_date.isoformat()))
        event_number = meta.event_number if meta else None
        canonical = CanonicalVolunteerResult(
            external_result_key=s95_volunteer_key(slug, event_date, user_id, role),
            event_date=event_date,
            external_user_id=user_id,
            participant_name=participant_name,
            role=role,
            location_external_key=slug,
            location_name=lookups.name_by_slug.get(slug, name),
            event_number=event_number,
        )

        if batch_date != event_date or batch_slug != slug:
            flush_batch()
            if not dry_run:
                db.commit()
            batch_event_id = _ensure_event(
                db,
                platform,
                target,
                lookups,
                name_point=name,
                event_date=event_date,
                dry_run=dry_run,
            )
            batch_date = event_date
            batch_slug = slug

        batch.append(canonical)
        if len(batch) >= batch_size:
            flush_batch()
            if not dry_run:
                db.commit()

    flush_batch()
    if not dry_run:
        db.commit()
    return stats


def migrate_all(
    db: Session,
    conn,
    *,
    dry_run: bool,
    batch_size: int,
    limit: int | None,
    steps: set[str] | None = None,
) -> MigrationStats:
    steps = steps or {"locations", "events", "participants", "runs", "volunteers"}
    stats = MigrationStats()
    lookups = S95Lookups()
    lookups.load_from_legacy(conn)

    if "locations" in steps:
        loc_stats = migrate_s95_locations(db, conn, dry_run=dry_run, stats=MigrationStats())
        stats.merge(loc_stats)
        if not dry_run:
            db.commit()

    if "events" in steps:
        migrate_events(db, conn, lookups, dry_run=dry_run, stats=stats)
        if not dry_run:
            db.commit()

    if "participants" in steps:
        migrate_participants(db, conn, dry_run=dry_run, limit=limit, stats=stats)

    if "runs" in steps:
        migrate_runs(db, conn, lookups, dry_run=dry_run, batch_size=batch_size, limit=limit, stats=stats)

    if "volunteers" in steps:
        migrate_volunteers(
            db,
            conn,
            lookups,
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            stats=stats,
        )
    return stats
