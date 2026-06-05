from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.migration.helpers import (
    five_verst_event_key,
    five_verst_run_key,
    five_verst_unknown_user_id,
    five_verst_volunteer_key,
    is_five_verst_unknown_runner,
    legacy_time_display,
    legacy_time_to_seconds,
    legacy_timestamp_to_date,
    parse_float,
    slug_from_5verst_url,
)
from app.migration.legacy_db import legacy_row_stream, legacy_rows
from app.migration.lookups import FiveVerstLookups, TargetLookups
from app.migration.stats import MigrationStats
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.models import Event
from app.platform_adapters.five_verst import bulk_parser
from app.sync import upsert

PLATFORM_CODE = "five_verst"


def migrate_locations(
    db: Session,
    conn,
    lookups: FiveVerstLookups | None = None,
    *,
    dry_run: bool,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    target = TargetLookups(db, platform)
    migrated_slugs: set[str] = set()

    for row in legacy_rows(
        conn,
        """
        SELECT
            link_point,
            name_point,
            city,
            region,
            latitude,
            longitude,
            is_pause
        FROM general_location
        WHERE link_point IS NOT NULL AND name_point IS NOT NULL
        ORDER BY name_point
        """,
    ):
        external_key = slug_from_5verst_url(str(row["link_point"]))
        if not external_key:
            stats.skipped += 1
            stats.add_error(f"location: cannot parse slug from {row['link_point']!r}")
            continue

        location = CanonicalLocation(
            external_key=external_key,
            name=str(row["name_point"]).strip(),
            country="Россия",
            city=(str(row["city"]).strip() if row.get("city") else None),
            region=(str(row["region"]).strip() if row.get("region") else None),
            latitude=parse_float(row.get("latitude")),
            longitude=parse_float(row.get("longitude")),
            source_url=str(row["link_point"]).strip(),
        )
        if dry_run:
            stats.locations += 1
            migrated_slugs.add(external_key)
            continue

        location_row, _ = upsert.upsert_location(db, platform, location)
        if row.get("is_pause"):
            location_row.is_paused = bool(row["is_pause"])
        target.remember_location(location_row)
        migrated_slugs.add(external_key)
        stats.locations += 1

    if lookups is not None:
        for slug, name in lookups.name_by_slug.items():
            if slug in migrated_slugs:
                continue
            location = CanonicalLocation(
                external_key=slug,
                name=name,
                country="Россия",
                source_url=f"https://5verst.ru/{slug}/",
            )
            if dry_run:
                stats.locations += 1
                continue
            location_row, _ = upsert.upsert_location(db, platform, location)
            target.remember_location(location_row)
            stats.locations += 1

    if not dry_run:
        db.flush()
        target.preload_locations()
    return stats


def migrate_events(
    db: Session,
    conn,
    lookups: FiveVerstLookups,
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
            is_test,
            count_runners,
            count_vol,
            mean_time,
            best_time_woman,
            best_time_man,
            link_event
        FROM list_all_events
        WHERE name_point IS NOT NULL AND date_event IS NOT NULL
        ORDER BY date_event, name_point
        """,
    ):
        name = str(row["name_point"]).strip()
        slug = lookups.resolve_slug(name)
        if not slug and row.get("link_event"):
            slug = slug_from_5verst_url(str(row["link_event"]))
            if slug:
                lookups.register_name_slug(name, slug)
        if not slug:
            stats.skipped += 1
            stats.add_error(f"event: unknown location name {name!r}")
            continue

        event_date = legacy_timestamp_to_date(row["date_event"])
        if event_date is None:
            stats.skipped += 1
            continue

        index_event = row.get("index_event")
        event_number = int(index_event) if index_event is not None else None
        external_event_key = five_verst_event_key(slug, event_date, event_number)
        avg_sec = legacy_time_to_seconds(row.get("mean_time"))
        best_female_sec = legacy_time_to_seconds(row.get("best_time_woman"))
        best_male_sec = legacy_time_to_seconds(row.get("best_time_man"))
        summary = CanonicalEventSummary(
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=event_number,
            location_external_key=slug,
            location_name=lookups.name_by_slug.get(slug, name),
            finishers_count=int(row["count_runners"]) if row.get("count_runners") is not None else None,
            volunteers_count=int(row["count_vol"]) if row.get("count_vol") is not None else None,
            avg_time_sec=avg_sec,
            avg_time_display=legacy_time_display(row.get("mean_time"), avg_sec),
            best_female_time_sec=best_female_sec,
            best_female_time_display=legacy_time_display(row.get("best_time_woman"), best_female_sec),
            best_male_time_sec=best_male_sec,
            best_male_time_display=legacy_time_display(row.get("best_time_man"), best_male_sec),
            is_test_event=bool(row.get("is_test")),
            source_url=(str(row["link_event"]).strip() if row.get("link_event") else ""),
            summary_hash=bulk_parser.compute_summary_hash(
                event_number=event_number,
                event_date=event_date,
                finishers_count=int(row["count_runners"]) if row.get("count_runners") is not None else None,
                volunteers_count=int(row["count_vol"]) if row.get("count_vol") is not None else None,
                avg_time_sec=avg_sec,
                best_female_time_sec=best_female_sec,
                best_male_time_sec=best_male_sec,
            ),
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
                    country="Россия",
                    source_url=f"https://5verst.ru/{slug}/",
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


def _ensure_event(
    db: Session,
    platform,
    target: TargetLookups,
    lookups: FiveVerstLookups,
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
    is_test = meta.is_test_event if meta else False
    source_url = meta.source_url if meta and meta.source_url else (
        f"https://5verst.ru/{slug}/results/{event_date.strftime('%d.%m.%Y')}/"
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
                country="Россия",
                source_url=f"https://5verst.ru/{slug}/",
            ),
        )
        target.remember_location(location)

    external_event_key = five_verst_event_key(slug, event_date, event_number)
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
        is_test_event=is_test,
    )
    target.remember_event(slug, event)
    return event.id


def migrate_runs(
    db: Session,
    conn,
    lookups: FiveVerstLookups,
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
            age_category,
            status_runner
        FROM details_protocol
        WHERE name_point IS NOT NULL
          AND date_event IS NOT NULL
          AND (
            (user_id IS NOT NULL AND TRIM(user_id) <> '')
            OR status_runner = 'unknown_runner'
            OR name_runner ILIKE '%неизвест%'
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
    processed = 0

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

    rows = legacy_row_stream(conn, sql, params)
    for row in rows:
        processed += 1
        name = str(row["name_point"]).strip()
        event_date = legacy_timestamp_to_date(row["date_event"])
        if event_date is None:
            stats.skipped += 1
            continue

        slug = lookups.resolve_slug(name)
        if not slug:
            stats.skipped += 1
            stats.add_error(f"run: unknown location {name!r}")
            continue

        position_raw = row.get("position")
        position = int(position_raw) if position_raw is not None else None
        name_runner = str(row["name_runner"]).strip() if row.get("name_runner") else None
        status_runner = str(row["status_runner"]).strip() if row.get("status_runner") else None
        user_id_raw = str(row["user_id"]).strip() if row.get("user_id") else ""
        is_unknown = is_five_verst_unknown_runner(
            user_id=user_id_raw or None,
            name_runner=name_runner,
            status_runner=status_runner,
        )
        if not user_id_raw and not is_unknown:
            stats.skipped += 1
            continue
        if is_unknown:
            if position is None:
                stats.skipped += 1
                continue
            user_id = five_verst_unknown_user_id(slug, event_date, position)
            participant_name = name_runner or "НЕИЗВЕСТНЫЙ"
            run_status = status_runner or "unknown_runner"
        else:
            user_id = user_id_raw
            participant_name = name_runner or f"User {user_id}"
            run_status = status_runner

        finish_sec = legacy_time_to_seconds(row.get("finish_time"))
        canonical = CanonicalRunResult(
            external_result_key=five_verst_run_key(slug, event_date, user_id),
            event_date=event_date,
            external_user_id=user_id,
            participant_name=participant_name,
            position=position,
            finish_time_sec=finish_sec,
            finish_time_display=legacy_time_display(row.get("finish_time"), finish_sec),
            age_category=(str(row["age_category"]).strip() if row.get("age_category") else None),
            status=run_status,
            location_external_key=slug,
            location_name=lookups.name_by_slug.get(slug, name),
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
    lookups: FiveVerstLookups,
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
        FROM details_vol
        WHERE name_point IS NOT NULL
          AND date_event IS NOT NULL
          AND user_id IS NOT NULL
          AND TRIM(user_id) <> ''
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
        nonlocal batch, batch_event_id
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
            continue

        user_id = str(row["user_id"]).strip()
        role = str(row["vol_role"]).strip() if row.get("vol_role") else "volunteer"
        canonical = CanonicalVolunteerResult(
            external_result_key=five_verst_volunteer_key(slug, event_date, user_id, role),
            event_date=event_date,
            external_user_id=user_id,
            participant_name=(str(row["name_runner"]).strip() if row.get("name_runner") else f"User {user_id}"),
            role=role,
            source_url=f"https://5verst.ru/{slug}/results/{event_date.strftime('%d.%m.%Y')}/",
            location_external_key=slug,
            location_name=lookups.name_by_slug.get(slug, name),
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
    steps = steps or {"locations", "events", "runs", "volunteers"}
    stats = MigrationStats()
    lookups = FiveVerstLookups()
    lookups.load_from_legacy(conn)

    if "locations" in steps:
        migrate_locations(db, conn, lookups, dry_run=dry_run, stats=stats)
        if not dry_run:
            db.commit()
    if "events" in steps:
        migrate_events(db, conn, lookups, dry_run=dry_run, stats=stats)
        if not dry_run:
            db.commit()
    if "runs" in steps:
        migrate_runs(db, conn, lookups, dry_run=dry_run, batch_size=batch_size, limit=limit, stats=stats)
    if "volunteers" in steps:
        migrate_volunteers(db, conn, lookups, dry_run=dry_run, batch_size=batch_size, limit=limit, stats=stats)
    return stats
