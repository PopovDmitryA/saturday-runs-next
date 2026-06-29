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
    """Resolve a barcode to a RunPark participant_id, import only THAT participant's events
    into our DB (not the whole location), and return the Participant or None.

    Preview must stay cheap: importing every event of every tracked location synchronously
    (thousands of remote MSSQL round-trips) froze the request. We reuse the same scoped,
    participant-only import that the user-sync worker runs.
    """
    import logging

    from app.models import Participant
    from app.runpark.mssql_client import runpark_query
    from app.sync.runpark_global_sync import sync_runpark_for_participant

    log = logging.getLogger(__name__)

    try:
        run_rows = runpark_query(
            "SELECT TOP 1 participant_id FROM api.vw_run_results WHERE barcode_id = %s",
            (barcode_id,),
        )
        if not run_rows:
            vol_rows = runpark_query(
                "SELECT TOP 1 participant_id FROM api.vw_volunteer_results WHERE barcode_id = %s",
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

    try:
        sync_runpark_for_participant(db, participant_id)
    except Exception as exc:
        log.exception("Error importing RunPark participant %s during barcode lookup: %s", participant_id, exc)

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
