from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Participant,
    Platform,
    ProtocolSyncState,
    RunResult,
    SyncStatus,
    VolunteerResult,
)
from app.platform_adapters.canonical import CanonicalEventSummary
from app.s95.api_client import S95ApiActivityRef, fetch_activity
from app.s95.parsers.api_protocol import ParsedApiProtocol, parse_s95_activity
from app.sync import upsert


@dataclass
class S95ApiProtocolResult:
    external_event_key: str
    created: bool
    changed: bool
    run_results_count: int
    volunteer_results_count: int


def _content_hash(parsed: ParsedApiProtocol) -> str:
    parts: list[str] = []
    for r in sorted(parsed.run_results, key=lambda x: x.external_result_key):
        parts.append(f"{r.external_result_key}|{r.position}|{r.finish_time_sec}|{r.club_name or ''}")
    for v in sorted(parsed.volunteer_results, key=lambda x: x.external_result_key):
        parts.append(f"{v.external_result_key}|{v.role}")
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _get_or_create_state(db: Session, *, event_id, event_summary_id) -> ProtocolSyncState:
    row = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event_id).one_or_none()
    if row is None:
        row = ProtocolSyncState(event_id=event_id, event_summary_id=event_summary_id)
        db.add(row)
        db.flush()
    elif event_summary_id and row.event_summary_id != event_summary_id:
        row.event_summary_id = event_summary_id
    return row


def _store_athlete_codes(db: Session, platform: Platform, parsed: ParsedApiProtocol) -> None:
    if not parsed.athlete_codes:
        return
    rows = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id.in_(list(parsed.athlete_codes.keys())),
        )
        .all()
    )
    for row in rows:
        codes = parsed.athlete_codes.get(row.external_user_id)
        if not codes:
            continue
        extra = dict(row.profile_extra or {})
        existing = dict(extra.get("platform_codes") or {})
        existing.update(codes)
        extra["platform_codes"] = existing
        row.profile_extra = extra
    db.flush()


def upsert_activity_protocol_api(
    db: Session,
    platform: Platform,
    location,
    activity_ref: S95ApiActivityRef,
    *,
    activity_json: dict | None = None,
    recalculate_pr: bool = True,
) -> S95ApiProtocolResult:
    """Atomically upsert one protocol from the JSON API.

    The caller is responsible for commit/rollback per protocol so a failure mid-parse
    leaves no 'seen' marker (Event/EventSummary) behind — it will be retried next run.

    recalculate_pr keeps personal records current as each protocol is written (ongoing
    syncs); pass False for bulk backfill and recompute once at the end.
    """
    activity = activity_json if activity_json is not None else fetch_activity(activity_ref.url)
    parsed = parse_s95_activity(
        activity,
        location_external_key=location.external_key,
        location_name=location.name,
        source_url=activity_ref.url,
    )
    content_hash = _content_hash(parsed)
    external_event_key = f"{location.external_key}:{parsed.event_date.isoformat()}"

    existing_event = (
        db.query(Event)
        .filter(Event.platform_id == platform.id, Event.external_event_key == external_event_key)
        .one_or_none()
    )

    # Unchanged fast-path: protocol content identical to last fetch — only bump check time.
    if existing_event is not None:
        state = (
            db.query(ProtocolSyncState)
            .filter(ProtocolSyncState.event_id == existing_event.id)
            .one_or_none()
        )
        if state is not None and state.protocol_source_hash == content_hash:
            state.last_protocol_check_at = datetime.now(timezone.utc)
            db.flush()
            return S95ApiProtocolResult(
                external_event_key=external_event_key,
                created=False,
                changed=False,
                run_results_count=state.run_results_count or 0,
                volunteer_results_count=state.volunteer_results_count or 0,
            )

    summary = CanonicalEventSummary(
        external_event_key=external_event_key,
        event_date=parsed.event_date,
        event_number=None,
        location_external_key=location.external_key,
        location_name=location.name,
        source_url=activity_ref.url,
        summary_hash=content_hash,
    )
    summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
    event_row = upsert.upsert_event_for_summary(db, platform, location, summary, summary_row)

    upsert.replace_event_run_results(
        db, event_row, platform, parsed.run_results, recalculate_pr=recalculate_pr
    )
    upsert.replace_event_volunteer_results(db, event_row, platform, parsed.volunteer_results)
    _store_athlete_codes(db, platform, parsed)

    run_count = db.query(RunResult).filter(RunResult.event_id == event_row.id).count()
    vol_count = db.query(VolunteerResult).filter(VolunteerResult.event_id == event_row.id).count()

    state = _get_or_create_state(db, event_id=event_row.id, event_summary_id=summary_row.id)
    now = datetime.now(timezone.utc)
    state.last_protocol_fetched_at = now
    state.last_protocol_check_at = now
    state.protocol_source_hash = content_hash
    state.run_results_count = run_count
    state.volunteer_results_count = vol_count

    summary_row.sync_status = SyncStatus.ok
    summary_row.error_message = None
    event_row.source_url = activity_ref.url
    event_row.fetched_at = now
    db.flush()

    return S95ApiProtocolResult(
        external_event_key=external_event_key,
        created=existing_event is None,
        changed=True,
        run_results_count=run_count,
        volunteer_results_count=vol_count,
    )
