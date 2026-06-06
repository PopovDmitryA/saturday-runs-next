from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    EventSummary,
    Location,
    Platform,
    ProtocolSyncState,
    RunResult,
    SyncStatus,
    VolunteerResult,
)
from app.platform_adapters.canonical import CanonicalEventSummary
from app.platform_adapters.five_verst import bulk_parser
from app.sync import upsert


@dataclass
class ProtocolUpsertResult:
    event_id: str
    run_results_upserted: int
    volunteer_results_upserted: int
    run_results_count: int
    volunteer_results_count: int
    protocol_source_hash: str
    protocol_changed: bool


def _get_or_create_protocol_sync_state(
    db: Session,
    *,
    event_id,
    event_summary_id,
) -> ProtocolSyncState:
    row = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event_id).one_or_none()
    if row is None:
        row = ProtocolSyncState(event_id=event_id, event_summary_id=event_summary_id)
        db.add(row)
        db.flush()
    elif event_summary_id and row.event_summary_id != event_summary_id:
        row.event_summary_id = event_summary_id
    return row


def _touch_protocol_check(db: Session, state: ProtocolSyncState) -> None:
    now = datetime.now(timezone.utc)
    state.last_protocol_check_at = now
    db.flush()


def mark_protocol_check(db: Session, event_id) -> None:
    state = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event_id).one_or_none()
    if state is None:
        state = ProtocolSyncState(event_id=event_id)
        db.add(state)
    _touch_protocol_check(db, state)


def fetch_and_upsert_event_protocol(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
    summary_row: EventSummary,
) -> ProtocolUpsertResult:
    slug = summary.location_external_key
    run_results, protocol_html = bulk_parser.fetch_run_protocol(
        slug,
        summary.event_date,
        summary.event_number,
    )
    volunteer_results, _ = bulk_parser.fetch_volunteers(
        slug,
        summary.event_date,
        summary.event_number,
    )
    protocol_source_hash = bulk_parser.source_hash(protocol_html)
    event_row = upsert.upsert_event_for_summary(db, platform, location, summary, summary_row)
    state = _get_or_create_protocol_sync_state(
        db,
        event_id=event_row.id,
        event_summary_id=summary_row.id,
    )
    previous_hash = state.protocol_source_hash
    run_results_upserted = upsert.upsert_run_results(db, event_row, platform, run_results)
    volunteer_results_upserted = upsert.upsert_volunteer_results(db, event_row, platform, volunteer_results)
    run_results_count = (
        db.query(RunResult).filter(RunResult.event_id == event_row.id).count()
    )
    volunteer_results_count = (
        db.query(VolunteerResult).filter(VolunteerResult.event_id == event_row.id).count()
    )
    now = datetime.now(timezone.utc)
    state.last_protocol_fetched_at = now
    state.last_protocol_check_at = now
    state.protocol_source_hash = protocol_source_hash
    state.finishers_at_fetch = summary.finishers_count
    state.run_results_count = run_results_count
    state.volunteer_results_count = volunteer_results_count
    summary_row.sync_status = SyncStatus.ok
    summary_row.error_message = None
    db.flush()
    return ProtocolUpsertResult(
        event_id=str(event_row.id),
        run_results_upserted=run_results_upserted,
        volunteer_results_upserted=volunteer_results_upserted,
        run_results_count=run_results_count,
        volunteer_results_count=volunteer_results_count,
        protocol_source_hash=protocol_source_hash,
        protocol_changed=previous_hash is not None and previous_hash != protocol_source_hash,
    )
