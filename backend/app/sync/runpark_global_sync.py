from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

_BARCODE_RE = re.compile(r"^A?\d{4,}$", re.IGNORECASE)

from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventCrosslink,
    Location,
    Platform,
    RunparkLocationMapping,
    RunResult,
    SyncRun,
    SyncRunStatus,
    VolunteerResult,
)
from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.runpark.mssql_client import fix_varchar_encoding, runpark_query
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step

logger = logging.getLogger(__name__)

PLATFORM_CODE = "runpark"


@dataclass
class RunparkSyncResult:
    events_total: int = 0
    events_upserted: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform, sync_type: str) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type=sync_type,
        status=SyncRunStatus.running,
        parser_version=upsert.PARSER_VERSION,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _finish_sync_run(db: Session, run: SyncRun, *, success: bool, error: str | None = None) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error
    db.flush()


def _get_location_mapping(db: Session) -> dict[str, Location]:
    """Returns {runpark_location_id (upper) -> Location} for show_on_map, dual_load and transitioned_to_primary locations."""
    rows = (
        db.query(RunparkLocationMapping)
        .filter(
            (RunparkLocationMapping.show_on_map == True)  # noqa: E712
            | (RunparkLocationMapping.decision == "dual_load")
            | (RunparkLocationMapping.decision == "transitioned_to_primary")
        )
        .all()
    )
    result: dict[str, Location] = {}
    for m in rows:
        if m.runpark_location_row is not None:
            result[m.runpark_location_id.upper()] = m.runpark_location_row
    return result


def _build_mapping_index(db: Session) -> dict[str, RunparkLocationMapping]:
    """{runpark_location_id (upper) -> mapping} for ALL RunPark locations."""
    return {m.runpark_location_id.upper(): m for m in db.query(RunparkLocationMapping).all()}


def _resolve_participant_location(
    db: Session,
    platform: Platform,
    mapping: RunparkLocationMapping,
    cache: dict[str, Location | None],
) -> Location | None:
    """Return the RunPark Location for a mapping, materialising a hidden one if needed.

    Locations that are duplicates of an existing primary park (decision=remove_from_runpark /
    transitional) have no standalone Location and are not shown on the map. To still import a
    participant's full run history there, we create a hidden Location (is_official_map=False)
    on demand and back-fill the mapping so crosslinking can find it. Events imported here are
    crosslinked to the primary park (see _upsert_crosslinks_for_event), so they are deduped
    out of totals when the user also has the primary 5verst result.
    """
    key = mapping.runpark_location_id.upper()
    if key in cache:
        return cache[key]

    location = mapping.runpark_location_row
    if location is None and mapping.runpark_slug:
        location = (
            db.query(Location)
            .filter(Location.platform_id == platform.id, Location.external_key == mapping.runpark_slug)
            .one_or_none()
        )
        if location is None:
            location = Location(
                platform_id=platform.id,
                external_key=mapping.runpark_slug,
                name=mapping.display_name or mapping.runpark_name,
                country=mapping.country,
                city=mapping.city,
                region=mapping.region,
                latitude=mapping.latitude,
                longitude=mapping.longitude,
                is_official_map=False,  # duplicate of a primary park — hidden from map/lists
                source_url=mapping.public_url,
            )
            db.add(location)
            db.flush()
        if mapping.runpark_location_row_id != location.id:
            mapping.runpark_location_row_id = location.id
            db.flush()

    cache[key] = location
    return location


def _upsert_crosslinks_for_event(db: Session, runpark_event: Event) -> None:
    """For a RunPark event at a location that duplicates a primary park: find the matching
    primary-platform event by date and crosslink it (RunPark event = secondary)."""
    mapping = (
        db.query(RunparkLocationMapping)
        .filter(RunparkLocationMapping.runpark_location_row_id == runpark_event.location_id)
        .first()
    )
    if mapping is None or mapping.matched_location_id is None:
        return

    primary_event = (
        db.query(Event)
        .filter(
            Event.location_id == mapping.matched_location_id,
            Event.event_date == runpark_event.event_date,
            Event.is_test_event.is_(False),
        )
        .first()
    )
    if primary_event is None:
        return

    exists = (
        db.query(EventCrosslink)
        .filter(
            EventCrosslink.primary_event_id == primary_event.id,
            EventCrosslink.secondary_event_id == runpark_event.id,
        )
        .first()
    )
    if exists is None:
        db.add(EventCrosslink(primary_event_id=primary_event.id, secondary_event_id=runpark_event.id))
        db.flush()


def _ensure_event(db: Session, platform: Platform, location: Location, row: dict) -> Event:
    external_event_key = str(row["event_id"]).upper()
    event_date = row["event_date"].date() if hasattr(row["event_date"], "date") else row["event_date"]
    event_number = row.get("event_number") or None
    title = f"{location.name} #{event_number}" if event_number else location.name

    existing = (
        db.query(Event)
        .filter(Event.platform_id == platform.id, Event.external_event_key == external_event_key)
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if existing is None:
        # Check by (platform, location, date) to handle RunPark's duplicate event_ids per day
        existing_by_date = (
            db.query(Event)
            .filter(
                Event.platform_id == platform.id,
                Event.location_id == location.id,
                Event.event_date == event_date,
            )
            .first()
        )
        if existing_by_date is not None:
            logger.warning(
                "RunPark: duplicate event date %s for location %s — merging %s into existing %s",
                event_date, location.name, external_event_key, existing_by_date.external_event_key,
            )
            return existing_by_date

        existing = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=external_event_key,
            event_date=event_date,
            event_number=event_number,
            is_test_event=bool(row.get("is_test_event")),
            title=title,
            finishers_count=row.get("finishers_count"),
            runners_count=row.get("finishers_count"),
            fetched_at=now,
        )
        db.add(existing)
        db.flush()
    else:
        existing.event_number = event_number
        existing.finishers_count = row.get("finishers_count")
        existing.runners_count = row.get("finishers_count")
        existing.fetched_at = now
        db.flush()
    return existing


def _delete_event_results(db: Session, event: Event) -> None:
    db.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).delete()
    db.query(RunResult).filter(RunResult.event_id == event.id).delete()
    db.flush()


def _external_user_id(row: dict, result_id: str) -> str:
    if row.get("participant_id"):
        return str(row["participant_id"]).upper()
    if row.get("barcode_id"):
        return f"barcode:{row['barcode_id']}"
    return f"anon:{result_id}"


def _to_canonical_run(row: dict) -> CanonicalRunResult:
    result_id = str(row["result_id"]).upper()
    name = fix_varchar_encoding(row.get("participant_name")) or "Неизвестный бегун"
    return CanonicalRunResult(
        external_result_key=result_id,
        event_date=row["event_date"].date() if hasattr(row["event_date"], "date") else row["event_date"],
        external_user_id=_external_user_id(row, result_id),
        participant_name=name,
        position=row.get("position"),
        finish_time_sec=row.get("finish_time_sec"),
        finish_time_display=row.get("finish_time_display"),
        age_category=row.get("age_category"),
        status=row.get("status"),
        is_pr=bool(row.get("is_pr")),
        barcode_id=row["barcode_id"] if row.get("barcode_id") and _BARCODE_RE.match(str(row["barcode_id"])) else None,
    )


def _to_canonical_volunteer(row: dict, event_id_str: str) -> CanonicalVolunteerResult | None:
    participant_id = str(row["participant_id"]).upper() if row.get("participant_id") else None
    role = fix_varchar_encoding(row.get("role")) or ""
    if not role:
        return None
    # Natural key: event_id:participant_id:role (volunteer_id is always NULL)
    valid_barcode = row.get("barcode_id") and _BARCODE_RE.match(str(row["barcode_id"]))
    key_participant = participant_id or (f"barcode:{row['barcode_id']}" if valid_barcode else "anon")
    external_result_key = f"{event_id_str}:{key_participant}:{role}"
    external_user_id = participant_id or (f"barcode:{row['barcode_id']}" if valid_barcode else None)
    return CanonicalVolunteerResult(
        external_result_key=external_result_key,
        event_date=row["event_date"].date() if hasattr(row["event_date"], "date") else row["event_date"],
        external_user_id=external_user_id,
        participant_name=fix_varchar_encoding(row.get("participant_name")),
        role=role,
    )


def sync_runpark_batch(db: Session, since_date: date) -> RunparkSyncResult:
    result = RunparkSyncResult()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    location_map = _get_location_mapping(db)

    if not location_map:
        logger.warning("No RunPark locations with show_on_map=true found")
        return result

    location_ids_sql = ", ".join(f"'{lid}'" for lid in location_map)
    sync_run = _start_sync_run(db, platform, f"runpark:batch:since:{since_date}")

    try:
        events = runpark_query(
            f"SELECT * FROM api.vw_events "
            f"WHERE event_date >= %s AND UPPER(CAST(location_id AS nvarchar(64))) IN ({location_ids_sql})",
            (since_date,),
        )
        result.events_total = len(events)
        logger.info("RunPark: %d events since %s", len(events), since_date)

        for ev in events:
            location_id_key = str(ev["location_id"]).upper()
            location = location_map.get(location_id_key)
            if location is None:
                logger.warning("No location mapping for runpark location_id=%s", ev["location_id"])
                continue

            external_event_key = str(ev["event_id"]).upper()
            try:
                event_row = _ensure_event(db, platform, location, ev)
                _delete_event_results(db, event_row)

                run_rows = runpark_query(
                    "SELECT * FROM api.vw_run_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
                    (external_event_key,),
                )
                canonical_runs = [_to_canonical_run(r) for r in run_rows]
                run_count = upsert.upsert_run_results(db, event_row, platform, canonical_runs, recalculate_pr=False)
                result.run_results_upserted += run_count

                vol_rows = runpark_query(
                    "SELECT * FROM api.vw_volunteer_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
                    (external_event_key,),
                )
                canonical_vols = [
                    v for r in vol_rows
                    if (v := _to_canonical_volunteer(r, external_event_key)) is not None
                ]
                vol_count = upsert.upsert_volunteer_results(db, event_row, platform, canonical_vols)
                result.volunteer_results_upserted += vol_count

                _upsert_crosslinks_for_event(db, event_row)
                result.events_upserted += 1
                commit_step(db)
                logger.info(
                    "RunPark event %s: %d runs, %d volunteers",
                    external_event_key, run_count, vol_count,
                )
            except Exception as exc:
                msg = f"Event {external_event_key}: {exc}"
                logger.exception(msg)
                result.errors.append(msg)
                rollback_step(db)

        _finish_sync_run(db, sync_run, success=not result.errors)
        db.commit()
    except Exception as exc:
        logger.exception("RunPark batch sync failed")
        _finish_sync_run(db, sync_run, success=False, error=str(exc))
        db.commit()
        result.errors.append(str(exc))

    return result


def sync_runpark_for_participant(db: Session, participant_id: str) -> RunparkSyncResult:
    """Sync all events for a specific participant (by RunPark participant_id UUID).

    Imports the participant's full RunPark history across ALL mapped locations — including
    parks that duplicate an existing 5verst/parkrun location. Those duplicate parks get a
    hidden Location and their events are crosslinked to the primary park, so the runs are
    deduped out of totals when the user also has the primary result.
    """
    result = RunparkSyncResult()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    mapping_index = _build_mapping_index(db)

    if not mapping_index:
        logger.warning("No RunPark location mappings found")
        return result

    location_cache: dict[str, Location | None] = {}
    pid_upper = participant_id.upper()

    # Find all event_ids where this participant has run or volunteered
    run_events = runpark_query(
        "SELECT DISTINCT event_id FROM api.vw_run_results WHERE UPPER(CAST(participant_id AS nvarchar(64))) = %s",
        (pid_upper,),
    )
    vol_events = runpark_query(
        "SELECT DISTINCT event_id FROM api.vw_volunteer_results WHERE UPPER(CAST(participant_id AS nvarchar(64))) = %s",
        (pid_upper,),
    )
    event_ids = {str(r["event_id"]).upper() for r in run_events + vol_events}

    if not event_ids:
        logger.info("RunPark: no events found for participant %s", pid_upper)
        return result

    result.events_total = len(event_ids)
    logger.info("RunPark user sync: %d events for participant %s", len(event_ids), pid_upper)

    for external_event_key in event_ids:
        events = runpark_query(
            "SELECT * FROM api.vw_events WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
            (external_event_key,),
        )
        if not events:
            continue
        ev = events[0]
        mapping = mapping_index.get(str(ev["location_id"]).upper())
        if mapping is None:
            continue  # location has no mapping at all

        loc_key = mapping.runpark_location_id.upper()
        try:
            # Resolve inside the try: materialising a hidden Location and the per-event
            # import share one transaction, so a failure rolls both back atomically.
            location = _resolve_participant_location(db, platform, mapping, location_cache)
            if location is None:
                continue  # mapping without a runpark_slug — cannot materialise a location
            event_row = _ensure_event(db, platform, location, ev)
            _delete_event_results(db, event_row)

            run_rows = runpark_query(
                "SELECT * FROM api.vw_run_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
                (external_event_key,),
            )
            canonical_runs = [_to_canonical_run(r) for r in run_rows]
            result.run_results_upserted += upsert.upsert_run_results(
                db, event_row, platform, canonical_runs, recalculate_pr=True
            )

            vol_rows = runpark_query(
                "SELECT * FROM api.vw_volunteer_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
                (external_event_key,),
            )
            canonical_vols = [
                v for r in vol_rows
                if (v := _to_canonical_volunteer(r, external_event_key)) is not None
            ]
            result.volunteer_results_upserted += upsert.upsert_volunteer_results(
                db, event_row, platform, canonical_vols
            )

            _upsert_crosslinks_for_event(db, event_row)
            result.events_upserted += 1
            commit_step(db)
        except Exception as exc:
            msg = f"Event {external_event_key}: {exc}"
            logger.exception(msg)
            result.errors.append(msg)
            rollback_step(db)
            # A location created in this rolled-back step is gone — drop the stale cache entry.
            location_cache.pop(loc_key, None)

    return result
