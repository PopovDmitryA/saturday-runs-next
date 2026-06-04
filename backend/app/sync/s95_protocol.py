from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState, RunResult, SyncStatus, VolunteerResult
from app.platform_adapters.canonical import CanonicalEventSummary
from app.s95.fetch import fetch_page_html
from app.s95.parsers.protocol import parse_protocol_page
from app.sync import upsert

# S95 event key: {location_slug}:{YYYY-MM-DD}; activity id lives in event_number / source_url.


@dataclass
class S95ProtocolUpsertResult:
    event_id: str
    run_results_upserted: int
    volunteer_results_upserted: int
    run_results_count: int
    volunteer_results_count: int
    protocol_source_hash: str
    protocol_changed: bool


def _protocol_source_hash(html: str) -> str:
    return hashlib.sha256(html.encode()).hexdigest()[:32]


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


def mark_protocol_check(db: Session, event_id) -> None:
    state = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event_id).one_or_none()
    if state is None:
        state = ProtocolSyncState(event_id=event_id)
        db.add(state)
    state.last_protocol_check_at = datetime.now(timezone.utc)
    db.flush()


def _apply_event_stats(summary_row: EventSummary, event_row: Event, event_stats) -> None:
    extra = event_stats.as_dict()
    if extra:
        summary_row.summary_extra = extra
    if event_stats.finishers_count is not None:
        summary_row.finishers_count = event_stats.finishers_count
        event_row.finishers_count = event_stats.finishers_count
        event_row.runners_count = event_stats.finishers_count
    if event_stats.volunteers_count is not None:
        summary_row.volunteers_count = event_stats.volunteers_count


def fetch_and_upsert_activity_protocol(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
    summary_row: EventSummary,
    *,
    protocol_url: str | None = None,
    protocol_html: str | None = None,
) -> S95ProtocolUpsertResult:
    url = protocol_url or summary.source_url
    if not url:
        raise ValueError(f"Protocol URL missing for {summary.external_event_key}")

    html = protocol_html or fetch_page_html(url, reason="activity_protocol")
    parsed = parse_protocol_page(
        html,
        url,
        location_external_key=summary.location_external_key,
        location_name=summary.location_name,
        event_date=summary.event_date,
        event_number=summary.event_number,
    )
    protocol_hash = _protocol_source_hash(html)
    event_row = upsert.upsert_event_for_summary(db, platform, location, summary, summary_row)
    state = _get_or_create_protocol_sync_state(
        db,
        event_id=event_row.id,
        event_summary_id=summary_row.id,
    )
    previous_hash = state.protocol_source_hash

    run_results_upserted = upsert.upsert_run_results(db, event_row, platform, parsed.run_results)
    volunteer_results_upserted = upsert.upsert_volunteer_results(
        db, event_row, platform, parsed.volunteer_results
    )
    _apply_event_stats(summary_row, event_row, parsed.event_stats)

    run_results_count = db.query(RunResult).filter(RunResult.event_id == event_row.id).count()
    volunteer_results_count = (
        db.query(VolunteerResult).filter(VolunteerResult.event_id == event_row.id).count()
    )
    now = datetime.now(timezone.utc)
    state.last_protocol_fetched_at = now
    state.last_protocol_check_at = now
    state.protocol_source_hash = protocol_hash
    state.finishers_at_fetch = summary_row.finishers_count
    state.run_results_count = run_results_count
    state.volunteer_results_count = volunteer_results_count
    summary_row.sync_status = SyncStatus.ok
    summary_row.error_message = None
    db.flush()
    return S95ProtocolUpsertResult(
        event_id=str(event_row.id),
        run_results_upserted=run_results_upserted,
        volunteer_results_upserted=volunteer_results_upserted,
        run_results_count=run_results_count,
        volunteer_results_count=volunteer_results_count,
        protocol_source_hash=protocol_hash,
        protocol_changed=previous_hash is not None and previous_hash != protocol_hash,
    )
