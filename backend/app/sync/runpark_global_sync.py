from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID

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
from app.runpark.mappings import runpark_protocol_base
from app.runpark.mssql_client import fix_varchar_encoding, runpark_query
from app.services.gender_position_service import recalculate_event_gender_positions
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step

logger = logging.getLogger(__name__)

PLATFORM_CODE = "runpark"

# RunPark проводит на одной локации в один день несколько разных стартов
# (ночной забег, детский, 3 км, эстафета…). В зачёт нашего сервиса идёт только
# основной субботний 5-километровый старт. Поле event_type_description добавили
# в vw_events / vw_run_results / vw_volunteer_results специально для нас; берём
# СТРОГО "5 км" — соседние значения ("5 км (дополнительный)", "3 км", …) не наши.
FIVE_KM_TYPE = "5 км"

# Единичный подтверждённый кейс (согласовано с владельцем RunPark): 27.04.2024
# в Михалково было два ОТДЕЛЬНЫХ старта "5 км" подряд утром — #113 в 08:00
# (6 финишёров) и #114 в 09:00 (11 финишёров), составы не пересекаются. Это не
# дубль одного протокола, а два разных забега, которые решено считать одним
# протоколом задним числом — 17 участников, места пересчитаны по факту финиша.
# Без этой записи _ensure_event сливал бы их по (location, date) и один старт
# просто затирал бы другой при каждой перезаливке (см. историю бага). Владелец
# RunPark сказал, что такое больше не планируется; если появится ещё один
# подобный день — добавить сюда отдельной записью primary -> [secondary, ...].
MERGED_EVENT_GROUPS: dict[str, list[str]] = {
    "847DF6D1-ABE2-4B46-98D6-57804896143D": ["2C207D24-F511-4FA2-9793-78597AE0E471"],
}
_SECONDARY_TO_PRIMARY: dict[str, str] = {
    secondary: primary for primary, secondaries in MERGED_EVENT_GROUPS.items() for secondary in secondaries
}


def _canonical_event_key(external_event_key: str) -> str:
    """Map a RunPark event_id to its merge-group primary key, if it's a known secondary
    (see MERGED_EVENT_GROUPS). Otherwise returns the key unchanged."""
    return _SECONDARY_TO_PRIMARY.get(external_event_key, external_event_key)


def _fetch_merged_run_rows(external_event_key: str) -> list[dict]:
    """Fetch '5 км' run rows for external_event_key. If it's a merge-group primary,
    also pulls its secondary event(s) and recomputes `position` across the combined
    field by finish time (see MERGED_EVENT_GROUPS)."""
    keys = [external_event_key, *MERGED_EVENT_GROUPS.get(external_event_key, [])]
    rows: list[dict] = []
    for key in keys:
        rows.extend(
            runpark_query(
                "SELECT * FROM api.vw_run_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s "
                "AND event_type_description = %s",
                (key, FIVE_KM_TYPE),
            )
        )
    if len(keys) > 1:
        rows.sort(key=lambda r: (r.get("finish_time_sec") is None, r.get("finish_time_sec") or 0))
        for position, row in enumerate(rows, start=1):
            row["position"] = position
    return rows


def _fix_merged_finishers_count(db: Session, event_row: Event, external_event_key: str, run_rows: list[dict]) -> None:
    """_ensure_event sets finishers_count from the primary's own vw_events row (e.g. 6),
    which undercounts a merge-group event — fix it to the actual combined total (17)."""
    if external_event_key not in MERGED_EVENT_GROUPS:
        return
    event_row.finishers_count = len(run_rows)
    event_row.runners_count = len(run_rows)
    db.flush()


def _fetch_merged_vol_rows(external_event_key: str) -> list[dict]:
    """Fetch '5 км' volunteer rows for external_event_key, merging in secondary
    event(s) for a merge-group primary (see MERGED_EVENT_GROUPS)."""
    keys = [external_event_key, *MERGED_EVENT_GROUPS.get(external_event_key, [])]
    rows: list[dict] = []
    for key in keys:
        rows.extend(
            runpark_query(
                "SELECT * FROM api.vw_volunteer_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s "
                "AND event_type_description = %s",
                (key, FIVE_KM_TYPE),
            )
        )
    return rows


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


def _get_location_mapping(db: Session) -> tuple[dict[str, Location], dict[UUID, str]]:
    """For show_on_map, dual_load and transitioned_to_primary locations, returns
    ({runpark_location_id (upper) -> Location}, {location.id -> protocol base url}).

    The protocol base (``.../Places/{slug}``) is stamped onto events.source_url so a
    "протокол на runpark.ru" link surfaces everywhere event source_url is shown.
    """
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
    protocol_bases: dict[UUID, str] = {}
    for m in rows:
        if m.runpark_location_row is not None:
            result[m.runpark_location_id.upper()] = m.runpark_location_row
            base = runpark_protocol_base(m.public_url, m.runpark_slug)
            if base is not None:
                protocol_bases[m.runpark_location_row.id] = base
    return result, protocol_bases


def _upsert_crosslinks_for_event(db: Session, runpark_event: Event) -> None:
    """For dual_load RunPark events: find matching primary-platform event by date and create crosslink."""
    mapping = (
        db.query(RunparkLocationMapping)
        .filter(
            RunparkLocationMapping.runpark_location_row_id == runpark_event.location_id,
            RunparkLocationMapping.decision == "dual_load",
        )
        .one_or_none()
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


def _recalculate_event_prs(db: Session, event_row: Event) -> None:
    """Recalculate is_pr for every participant of the event. Must run AFTER
    _upsert_crosslinks_for_event: the PR computation skips secondary-crosslink
    duplicates, so the crosslink has to exist before is_pr is assigned."""
    from app.services.personal_record_service import (
        recalculate_participants_cross_platform_personal_records,
        recalculate_participants_first_run_flags,
        recalculate_participants_personal_records,
    )

    participant_ids = {
        row[0]
        for row in db.query(RunResult.participant_id)
        .filter(RunResult.event_id == event_row.id, RunResult.participant_id.isnot(None))
        .all()
    }
    if participant_ids:
        recalculate_participants_first_run_flags(db, PLATFORM_CODE, participant_ids)
        recalculate_participants_personal_records(db, PLATFORM_CODE, participant_ids)
        recalculate_participants_cross_platform_personal_records(db, participant_ids)


def _ensure_event(
    db: Session,
    platform: Platform,
    location: Location,
    row: dict,
    protocol_base: str | None = None,
) -> Event:
    external_event_key = str(row["event_id"]).upper()
    event_date = row["event_date"].date() if hasattr(row["event_date"], "date") else row["event_date"]
    event_number = row.get("event_number") or None
    title = f"{location.name} #{event_number}" if event_number else location.name
    source_url = f"{protocol_base}/event/{event_number}" if protocol_base and event_number else None

    existing = (
        db.query(Event)
        .filter(Event.platform_id == platform.id, Event.external_event_key == external_event_key)
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if existing is None:
        # Fallback по (platform, location, date). Изначально был нужен из-за "дублей
        # event_id в один день" — на деле это были разные типы стартов (ночной, 3 км…),
        # которые теперь отсекаются фильтром FIVE_KM_TYPE ещё до сюда. Слияние опасно:
        # ниже по коду вызывается _delete_event_results, т.е. результаты первого события
        # затираются вторым. Среди 5км-стартов такой коллизии почти нет (на всю историю
        # один случай), поэтому fallback оставлен, но при срабатывании он логирует WARNING.
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
            source_url=source_url,
            fetched_at=now,
        )
        db.add(existing)
        db.flush()
    else:
        existing.event_number = event_number
        existing.finishers_count = row.get("finishers_count")
        existing.runners_count = row.get("finishers_count")
        if source_url:
            existing.source_url = source_url
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


def sync_runpark_batch(
    db: Session,
    since_date: date,
    *,
    only_location_ids: set[str] | None = None,
) -> RunparkSyncResult:
    """Sync 5км events since `since_date`.

    `only_location_ids` narrows the run to specific RunPark location_ids — used to
    backfill a freshly enabled location without re-syncing (and delete+reinsert-ing)
    every tracked location's whole history.
    """
    result = RunparkSyncResult()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    location_map, protocol_bases = _get_location_mapping(db)
    if only_location_ids is not None:
        wanted = {lid.upper() for lid in only_location_ids}
        location_map = {key: loc for key, loc in location_map.items() if key in wanted}

    if not location_map:
        logger.warning("No RunPark locations with show_on_map=true found")
        return result

    location_ids_sql = ", ".join(f"'{lid}'" for lid in location_map)
    sync_run = _start_sync_run(db, platform, f"runpark:batch:since:{since_date}")

    try:
        events = runpark_query(
            f"SELECT * FROM api.vw_events "
            f"WHERE event_date >= %s AND event_type_description = %s "
            f"AND UPPER(CAST(location_id AS nvarchar(64))) IN ({location_ids_sql})",
            (since_date, FIVE_KM_TYPE),
        )
        result.events_total = len(events)
        logger.info("RunPark: %d 5км events since %s", len(events), since_date)

        for ev in events:
            location_id_key = str(ev["location_id"]).upper()
            location = location_map.get(location_id_key)
            if location is None:
                logger.warning("No location mapping for runpark location_id=%s", ev["location_id"])
                continue

            external_event_key = str(ev["event_id"]).upper()
            if external_event_key in _SECONDARY_TO_PRIMARY:
                # Обрабатывается вместе со своим primary (см. MERGED_EVENT_GROUPS).
                continue
            try:
                event_row = _ensure_event(db, platform, location, ev, protocol_bases.get(location.id))
                _delete_event_results(db, event_row)

                run_rows = _fetch_merged_run_rows(external_event_key)
                _fix_merged_finishers_count(db, event_row, external_event_key, run_rows)
                canonical_runs = [_to_canonical_run(r) for r in run_rows]
                run_count = upsert.upsert_run_results(db, event_row, platform, canonical_runs, recalculate_pr=False)
                recalculate_event_gender_positions(db, event_row.id, PLATFORM_CODE)
                result.run_results_upserted += run_count

                vol_rows = _fetch_merged_vol_rows(external_event_key)
                canonical_vols = [
                    v for r in vol_rows
                    if (v := _to_canonical_volunteer(r, external_event_key)) is not None
                ]
                vol_count = upsert.upsert_volunteer_results(db, event_row, platform, canonical_vols)
                result.volunteer_results_upserted += vol_count

                _upsert_crosslinks_for_event(db, event_row)
                _recalculate_event_prs(db, event_row)
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
    """Sync all events for a specific participant (by RunPark participant_id UUID)."""
    result = RunparkSyncResult()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    location_map, protocol_bases = _get_location_mapping(db)

    if not location_map:
        logger.warning("No RunPark locations with show_on_map=true found")
        return result

    pid_upper = participant_id.upper()

    # Find all 5км event_ids where this participant has run or volunteered.
    run_events = runpark_query(
        "SELECT DISTINCT event_id FROM api.vw_run_results "
        "WHERE UPPER(CAST(participant_id AS nvarchar(64))) = %s AND event_type_description = %s",
        (pid_upper, FIVE_KM_TYPE),
    )
    vol_events = runpark_query(
        "SELECT DISTINCT event_id FROM api.vw_volunteer_results "
        "WHERE UPPER(CAST(participant_id AS nvarchar(64))) = %s AND event_type_description = %s",
        (pid_upper, FIVE_KM_TYPE),
    )
    # Канонизируем: если участник найден в secondary-событии из MERGED_EVENT_GROUPS,
    # обрабатываем его как участника primary-события (см. FIVE_KM_TYPE выше).
    event_ids = {_canonical_event_key(str(r["event_id"]).upper()) for r in run_events + vol_events}

    if not event_ids:
        logger.info("RunPark: no 5км events found for participant %s", pid_upper)
        return result

    result.events_total = len(event_ids)
    logger.info("RunPark user sync: %d events for participant %s", len(event_ids), pid_upper)

    for external_event_key in event_ids:
        events = runpark_query(
            "SELECT * FROM api.vw_events WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s "
            "AND event_type_description = %s",
            (external_event_key, FIVE_KM_TYPE),
        )
        if not events:
            continue
        ev = events[0]
        location_id_key = str(ev["location_id"]).upper()
        location = location_map.get(location_id_key)
        if location is None:
            continue  # not a tracked location

        try:
            event_row = _ensure_event(db, platform, location, ev, protocol_bases.get(location.id))
            _delete_event_results(db, event_row)

            run_rows = _fetch_merged_run_rows(external_event_key)
            _fix_merged_finishers_count(db, event_row, external_event_key, run_rows)
            canonical_runs = [_to_canonical_run(r) for r in run_rows]
            # recalculate_pr=False: PRs are recalculated after crosslinks are upserted
            # (below) so "не в зачёте" duplicates never get the PR marker.
            result.run_results_upserted += upsert.upsert_run_results(
                db, event_row, platform, canonical_runs, recalculate_pr=False
            )
            recalculate_event_gender_positions(db, event_row.id, PLATFORM_CODE)

            vol_rows = _fetch_merged_vol_rows(external_event_key)
            canonical_vols = [
                v for r in vol_rows
                if (v := _to_canonical_volunteer(r, external_event_key)) is not None
            ]
            result.volunteer_results_upserted += upsert.upsert_volunteer_results(
                db, event_row, platform, canonical_vols
            )

            _upsert_crosslinks_for_event(db, event_row)
            _recalculate_event_prs(db, event_row)
            result.events_upserted += 1
            commit_step(db)
        except Exception as exc:
            msg = f"Event {external_event_key}: {exc}"
            logger.exception(msg)
            result.errors.append(msg)
            rollback_step(db)

    return result
