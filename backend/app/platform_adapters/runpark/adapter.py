from __future__ import annotations

import re
from datetime import date

from sqlalchemy.orm import Session

from app.platform_adapters.base import AdapterCapabilities
from app.platform_adapters.canonical import (
    CanonicalEvent,
    CanonicalLocation,
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ExternalEventRef,
    ProfilePreview,
)

BARCODE_RE = re.compile(r"^A?\d+$", re.IGNORECASE)


class RunparkProfileNotFoundError(Exception):
    pass


class RunparkInvalidBarcodeError(Exception):
    pass


def _try_import_from_runpark(db: Session, platform: object, barcode_id: str) -> object:
    """Query RunPark MSSQL by barcode, import matching events into our DB, return Participant or None."""
    import logging

    from app.models import Participant, RunparkLocationMapping
    from app.runpark.mssql_client import runpark_query
    from app.sync import upsert
    from app.sync.runpark_global_sync import (
        _delete_event_results,
        _ensure_event,
        _to_canonical_run,
        _to_canonical_volunteer,
    )
    from app.sync.iteration_commit import commit_step, rollback_step

    log = logging.getLogger(__name__)

    try:
        run_rows = runpark_query(
            "SELECT TOP 1 participant_id, participant_name FROM api.vw_run_results WHERE barcode_id = %s",
            (barcode_id,),
        )
        if not run_rows:
            vol_rows = runpark_query(
                "SELECT TOP 1 participant_id, participant_name FROM api.vw_volunteer_results WHERE barcode_id = %s",
                (barcode_id,),
            )
            if not vol_rows:
                return None
            first = vol_rows[0]
        else:
            first = run_rows[0]
    except Exception as exc:
        log.warning("RunPark MSSQL lookup failed for barcode %s: %s", barcode_id, exc)
        return None

    participant_id = str(first["participant_id"]).upper() if first.get("participant_id") else None
    if not participant_id:
        return None

    location_map: dict[str, object] = {}
    mappings = (
        db.query(RunparkLocationMapping)
        .filter(
            (RunparkLocationMapping.show_on_map == True)  # noqa: E712
            | (RunparkLocationMapping.decision == "dual_load")
            | (RunparkLocationMapping.decision == "transitioned_to_primary")
        )
        .all()
    )
    for m in mappings:
        if m.runpark_location_row is not None:
            location_map[m.runpark_location_id.upper()] = m.runpark_location_row

    if not location_map:
        return None

    location_ids_sql = ", ".join(f"'{lid}'" for lid in location_map)

    try:
        events = runpark_query(
            f"SELECT * FROM api.vw_events "
            f"WHERE UPPER(CAST(location_id AS nvarchar(64))) IN ({location_ids_sql})"
        )
    except Exception as exc:
        log.warning("RunPark MSSQL events lookup failed: %s", exc)
        return None

    for ev in events:
        location_id_key = str(ev["location_id"]).upper()
        location = location_map.get(location_id_key)
        if location is None:
            continue
        external_event_key = str(ev["event_id"]).upper()
        try:
            event_row = _ensure_event(db, platform, location, ev)  # type: ignore[arg-type]
            _delete_event_results(db, event_row)

            er = runpark_query(
                "SELECT * FROM api.vw_run_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
                (external_event_key,),
            )
            upsert.upsert_run_results(db, event_row, platform, [_to_canonical_run(r) for r in er], recalculate_pr=False)  # type: ignore[arg-type]

            vr = runpark_query(
                "SELECT * FROM api.vw_volunteer_results WHERE UPPER(CAST(event_id AS nvarchar(64))) = %s",
                (external_event_key,),
            )
            canonical_vols = [
                v for r in vr if (v := _to_canonical_volunteer(r, external_event_key)) is not None
            ]
            upsert.upsert_volunteer_results(db, event_row, platform, canonical_vols)  # type: ignore[arg-type]
            commit_step(db)
        except Exception as exc:
            log.exception("Error importing RunPark event %s during barcode lookup: %s", external_event_key, exc)
            rollback_step(db)

    # Look up by participant_id UUID (external_user_id), not barcode — one participant can have many barcodes
    participant = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,  # type: ignore[attr-defined]
            Participant.external_user_id == participant_id,
        )
        .first()
    )
    return participant


def lookup_profile_preview_from_db(db: Session, barcode_id: str) -> ProfilePreview:
    """Look up a RunPark participant in our DB by barcode_id and return a ProfilePreview."""
    from app.models import Participant, Platform, RunResult, VolunteerResult

    stripped = barcode_id.strip().upper()
    # Accept both "A6871786" and "6871786" — normalize to "A..." form
    if stripped.isdigit():
        stripped = "A" + stripped
    normalized = stripped
    if not BARCODE_RE.match(normalized):
        raise RunparkInvalidBarcodeError(
            f"Некорректный штрихкод RunPark: «{barcode_id}». Формат: буква A и цифры, например A6871786."
        )

    platform = db.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if platform is None:
        raise RunparkProfileNotFoundError("Платформа RunPark не найдена в системе.")

    # Try by barcode_id first; if not found, resolve via participant_id UUID (one participant → many barcodes)
    participant = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.barcode_id == normalized,
        )
        .first()
    )
    if participant is None:
        # barcode might belong to a participant stored under a different barcode — resolve via RunPark
        try:
            from app.runpark.mssql_client import runpark_query as _rq
            rows = _rq(
                "SELECT TOP 1 participant_id FROM api.vw_run_results WHERE barcode_id = %s",
                (normalized,),
            )
            if not rows:
                rows = _rq(
                    "SELECT TOP 1 participant_id FROM api.vw_volunteer_results WHERE barcode_id = %s",
                    (normalized,),
                )
            if rows and rows[0].get("participant_id"):
                pid = str(rows[0]["participant_id"]).upper()
                participant = (
                    db.query(Participant)
                    .filter(
                        Participant.platform_id == platform.id,
                        Participant.external_user_id == pid,
                    )
                    .first()
                )
        except Exception:
            pass
    if participant is None:
        participant = _try_import_from_runpark(db, platform, normalized)
    if participant is None:
        raise RunparkProfileNotFoundError(
            f"Участник со штрихкодом {normalized} не найден ни в нашей базе, ни в базе RunPark. "
            "Проверьте штрихкод."
        )

    from sqlalchemy import func as sa_func

    from app.models import Event, Location
    from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
    from app.profile_preview import build_recent_preview_activities

    total_runs = (
        db.query(RunResult)
        .filter(RunResult.participant_id == participant.id)
        .count()
    )
    total_volunteering = (
        db.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .count()
    )
    last_run_date = (
        db.query(sa_func.max(Event.event_date))
        .join(RunResult, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id == participant.id)
        .scalar()
    )
    last_vol_date = (
        db.query(sa_func.max(Event.event_date))
        .join(VolunteerResult, VolunteerResult.event_id == Event.id)
        .filter(VolunteerResult.participant_id == participant.id)
        .scalar()
    )
    dates = [d for d in [last_run_date, last_vol_date] if d is not None]
    data_through = max(dates) if dates else None

    recent_runs_rows = (
        db.query(RunResult, Event, Location)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(RunResult.participant_id == participant.id)
        .order_by(Event.event_date.desc())
        .limit(5)
        .all()
    )
    recent_vol_rows = (
        db.query(VolunteerResult, Event, Location)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .filter(VolunteerResult.participant_id == participant.id)
        .order_by(Event.event_date.desc())
        .limit(5)
        .all()
    )
    canonical_runs = [
        CanonicalRunResult(
            external_result_key=rr.external_result_key,
            event_date=ev.event_date,
            external_user_id=participant.external_user_id,
            participant_name=participant.display_name or "",
            position=rr.position,
            finish_time_sec=rr.finish_time_sec,
            finish_time_display=rr.finish_time_display,
            location_name=loc.name,
        )
        for rr, ev, loc in recent_runs_rows
    ]
    canonical_vols = [
        CanonicalVolunteerResult(
            external_result_key=vr.external_result_key,
            event_date=ev.event_date,
            external_user_id=participant.external_user_id,
            participant_name=participant.display_name,
            role=vr.role or "",
            location_name=loc.name,
        )
        for vr, ev, loc in recent_vol_rows
    ]
    recent_activities = build_recent_preview_activities(canonical_runs, canonical_vols)

    return ProfilePreview(
        external_user_id=participant.external_user_id,
        display_name=participant.display_name or "Участник",
        profile_url=normalized,
        total_runs=total_runs,
        total_volunteering=total_volunteering,
        barcode_id=normalized,
        platform_code="runpark",
        data_source="database",
        data_updated_at=participant.fetched_at,
        data_through_date=data_through,
        recent_activities=recent_activities,
    )


class RunparkAdapter:
    platform_code = "runpark"
    parser_version = "0.1.0"
    capabilities = AdapterCapabilities(
        validate_profile_url=True,
        fetch_profile_preview=False,  # requires DB — handled by dedicated service function
    )

    def validate_profile_url(self, url: str) -> bool:
        return bool(BARCODE_RE.match(url.strip()))

    def fetch_profile_preview(self, url: str) -> ProfilePreview:
        raise NotImplementedError("RunPark preview requires DB access — use preview_runpark_profile_link()")

    def fetch_user_profile(self, external_user_id: str) -> CanonicalParticipant:
        raise NotImplementedError

    def fetch_user_runs(self, external_user_id: str) -> list[CanonicalRunResult]:
        raise NotImplementedError

    def fetch_user_volunteering(self, external_user_id: str) -> list[CanonicalVolunteerResult]:
        raise NotImplementedError

    def fetch_locations(self) -> list[CanonicalLocation]:
        raise NotImplementedError

    def fetch_events(self, since: date | None = None) -> list[CanonicalEvent]:
        raise NotImplementedError

    def fetch_event_results(self, event_ref: ExternalEventRef) -> list[CanonicalRunResult]:
        raise NotImplementedError

    def fetch_event_volunteers(self, event_ref: ExternalEventRef) -> list[CanonicalVolunteerResult]:
        raise NotImplementedError
