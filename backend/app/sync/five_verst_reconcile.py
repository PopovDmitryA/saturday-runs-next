from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState, RunResult, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalEventSummary
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol, mark_protocol_check

PLATFORM_CODE = "five_verst"


class ReconcileReason(str, Enum):
    never_checked = "never_checked"
    check_due = "check_due"
    count_mismatch = "count_mismatch"
    summary_changed = "summary_changed"


@dataclass(frozen=True)
class ReconcileCandidate:
    external_event_key: str
    location_external_key: str
    event_date: object
    reason: ReconcileReason


@dataclass
class ReconcileProtocolsOptions:
    dry_run: bool = False
    limit: int = 10
    min_check_interval_days: int = 7
    location_slug: str | None = None


@dataclass
class ReconcileProtocolsResult:
    candidates_total: int = 0
    protocols_fetched: int = 0
    protocols_changed: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    planned: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="five_verst:reconcile_protocols",
        status=SyncRunStatus.running,
        parser_version=upsert.PARSER_VERSION,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _finish_sync_run(
    db: Session,
    run: SyncRun,
    *,
    success: bool,
    fetched: int,
    upserted: int,
    error: str | None = None,
) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = fetched
    run.records_upserted = upserted
    run.error_message = error
    db.flush()


def _summary_to_canonical(summary_row: EventSummary, location: Location) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=summary_row.external_event_key,
        event_date=summary_row.event_date,
        event_number=summary_row.event_number,
        location_external_key=location.external_key,
        location_name=location.name,
        finishers_count=summary_row.finishers_count,
        volunteers_count=summary_row.volunteers_count,
        avg_time_sec=summary_row.avg_time_sec,
        avg_time_display=summary_row.avg_time_display,
        best_female_time_sec=summary_row.best_female_time_sec,
        best_female_time_display=summary_row.best_female_time_display,
        best_male_time_sec=summary_row.best_male_time_sec,
        best_male_time_display=summary_row.best_male_time_display,
        is_test_event=summary_row.is_test_event,
        source_url=summary_row.source_url or "",
        summary_hash=summary_row.summary_hash,
    )


def _classify_reconcile_reason(
    summary_row: EventSummary,
    state: ProtocolSyncState | None,
    run_count: int,
    *,
    check_cutoff: datetime,
) -> ReconcileReason | None:
    if state is None or state.last_protocol_check_at is None:
        return ReconcileReason.never_checked
    if state.last_protocol_check_at < check_cutoff:
        return ReconcileReason.check_due
    if summary_row.finishers_count is not None and state.finishers_at_fetch != summary_row.finishers_count:
        return ReconcileReason.summary_changed
    if summary_row.finishers_count is not None and run_count != summary_row.finishers_count:
        return ReconcileReason.count_mismatch
    return None


def plan_stale_protocol_reconcile(
    db: Session,
    *,
    limit: int = 10,
    min_check_interval_days: int = 7,
    location_slug: str | None = None,
) -> list[ReconcileCandidate]:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    check_cutoff = datetime.now(timezone.utc) - timedelta(days=min_check_interval_days)
    run_counts = (
        db.query(RunResult.event_id, func.count(RunResult.id).label("run_count"))
        .group_by(RunResult.event_id)
        .subquery()
    )
    query = (
        db.query(EventSummary, Location, ProtocolSyncState, run_counts.c.run_count)
        .join(Event, EventSummary.event_id == Event.id)
        .join(Location, EventSummary.location_id == Location.id)
        .outerjoin(ProtocolSyncState, ProtocolSyncState.event_id == Event.id)
        .outerjoin(run_counts, run_counts.c.event_id == Event.id)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.event_id.isnot(None),
        )
    )
    if location_slug:
        query = query.filter(Location.external_key == location_slug)

    rows = query.order_by(ProtocolSyncState.last_protocol_check_at.asc().nullsfirst()).all()
    candidates: list[ReconcileCandidate] = []
    for summary_row, location, state, run_count in rows:
        reason = _classify_reconcile_reason(
            summary_row,
            state,
            int(run_count or 0),
            check_cutoff=check_cutoff,
        )
        if reason is None:
            continue
        candidates.append(
            ReconcileCandidate(
                external_event_key=summary_row.external_event_key,
                location_external_key=location.external_key,
                event_date=summary_row.event_date,
                reason=reason,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def reconcile_stale_protocols(
    db: Session,
    options: ReconcileProtocolsOptions | None = None,
) -> ReconcileProtocolsResult:
    options = options or ReconcileProtocolsOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = ReconcileProtocolsResult()
    candidates = plan_stale_protocol_reconcile(
        db,
        limit=options.limit,
        min_check_interval_days=options.min_check_interval_days,
        location_slug=options.location_slug,
    )
    result.candidates_total = len(candidates)
    result.planned = [item.external_event_key for item in candidates]
    if options.dry_run:
        return result

    sync_run = _start_sync_run(db, platform)
    try:
        for candidate in candidates:
            summary_row = (
                db.query(EventSummary)
                .filter(
                    EventSummary.platform_id == platform.id,
                    EventSummary.external_event_key == candidate.external_event_key,
                )
                .one()
            )
            location = (
                db.query(Location)
                .filter(
                    Location.platform_id == platform.id,
                    Location.external_key == candidate.location_external_key,
                )
                .one()
            )
            summary = _summary_to_canonical(summary_row, location)
            try:
                upsert_result = fetch_and_upsert_event_protocol(
                    db,
                    platform,
                    location,
                    summary,
                    summary_row,
                )
                result.protocols_fetched += 1
                result.run_results_upserted += upsert_result.run_results_upserted
                result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
                if upsert_result.protocol_changed:
                    result.protocols_changed += 1
            except Exception as exc:
                result.errors.append(f"{candidate.external_event_key}: {exc}")
                if summary_row.event_id is not None:
                    mark_protocol_check(db, summary_row.event_id)

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.candidates_total,
            upserted=result.protocols_fetched,
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed = _start_sync_run(db, platform)
        _finish_sync_run(db, failed, success=False, fetched=0, upserted=0, error=str(exc))
        db.commit()
        raise
