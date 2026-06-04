from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform, SyncRun, SyncRunStatus, SyncStatus
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation
from app.s95.fetch import fetch_page_html
from app.s95.parsers.activities import parse_recent_activities_html
from app.sync import upsert
from app.sync.s95_protocol import fetch_and_upsert_activity_protocol
from app.sync.s95_summary_plan import (
    SummarySyncAction,
    SummarySyncPlanItem,
    plan_event_summaries_sync,
    plan_protocol_queue,
)

PLATFORM_CODE = "s95"
ACTIVITIES_URL = "https://s95.ru/activities"


@dataclass
class S95LatestSyncOptions:
    dry_run: bool = False
    update_limit: int | None = None
    protocol_fetch_limit: int = 0
    ensure_locations: bool = True
    fetch_all_protocols_on_change: bool = True


@dataclass
class S95LatestSyncResult:
    summaries_total: int = 0
    unchanged: int = 0
    new_summaries: int = 0
    changed_summaries: int = 0
    missing_protocol: int = 0
    needs_update: int = 0
    locations_created: int = 0
    locations_missing: int = 0
    summaries_upserted: int = 0
    protocols_fetched: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    planned_protocols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="s95:latest",
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
    unchanged: int,
    error: str | None = None,
) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = fetched
    run.records_upserted = upserted
    run.records_unchanged = unchanged
    run.error_message = error
    db.flush()


def fetch_latest_activity_summaries() -> list[CanonicalEventSummary]:
    html = fetch_page_html(ACTIVITIES_URL, reason="recent_activities")
    rows = parse_recent_activities_html(html, ACTIVITIES_URL)
    return [row.summary for row in rows]


def _ensure_location(
    db: Session,
    platform: Platform,
    summary: CanonicalEventSummary,
    *,
    result: S95LatestSyncResult,
) -> Location | None:
    row = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == summary.location_external_key,
        )
        .one_or_none()
    )
    if row is not None:
        if row.name != summary.location_name:
            row.name = summary.location_name
            row.source_url = f"https://s95.ru/events/{summary.location_external_key}"
            db.flush()
        return row

    canonical = CanonicalLocation(
        external_key=summary.location_external_key,
        name=summary.location_name,
        source_url=f"https://s95.ru/events/{summary.location_external_key}",
    )
    row, _ = upsert.upsert_location(db, platform, canonical)
    result.locations_created += 1
    db.flush()
    return row


def _collect_apply_items(plan: list[SummarySyncPlanItem], result: S95LatestSyncResult) -> list[SummarySyncPlanItem]:
    to_update: list[SummarySyncPlanItem] = []
    for item in plan:
        if item.action == SummarySyncAction.unchanged:
            result.unchanged += 1
        elif item.action == SummarySyncAction.new_summary:
            result.new_summaries += 1
            to_update.append(item)
        elif item.action == SummarySyncAction.changed_summary:
            result.changed_summaries += 1
            to_update.append(item)
        elif item.action == SummarySyncAction.missing_protocol:
            result.missing_protocol += 1
            to_update.append(item)
    result.needs_update = len(to_update)
    return to_update


def sync_s95_latest(
    db: Session,
    options: S95LatestSyncOptions | None = None,
) -> S95LatestSyncResult:
    options = options or S95LatestSyncOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95LatestSyncResult()

    summaries = fetch_latest_activity_summaries()
    result.summaries_total = len(summaries)
    plan = plan_event_summaries_sync(db, summaries)
    to_update = _collect_apply_items(plan, result)

    apply_items = to_update
    if options.update_limit is not None:
        apply_items = to_update[: options.update_limit]

    protocol_limit = max(options.protocol_fetch_limit, 0)
    protocol_queue = plan_protocol_queue(
        apply_items,
        protocol_fetch_limit=protocol_limit,
        fetch_all_protocols_on_change=options.fetch_all_protocols_on_change,
    )
    result.planned_protocols = [item.summary.external_event_key for item in protocol_queue]

    if options.dry_run:
        return result

    sync_run = _start_sync_run(db, platform)
    try:
        for item in apply_items:
            summary = item.summary
            if options.ensure_locations:
                location = _ensure_location(db, platform, summary, result=result)
                if location is None:
                    continue
            else:
                location = (
                    db.query(Location)
                    .filter(
                        Location.platform_id == platform.id,
                        Location.external_key == summary.location_external_key,
                    )
                    .one_or_none()
                )
                if location is None:
                    result.locations_missing += 1
                    result.errors.append(f"{summary.external_event_key}: location not in database")
                    continue

            summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
            if item.action != SummarySyncAction.unchanged:
                result.summaries_upserted += 1

        for item in protocol_queue:
            summary = item.summary
            location = (
                db.query(Location)
                .filter(
                    Location.platform_id == platform.id,
                    Location.external_key == summary.location_external_key,
                )
                .one_or_none()
            )
            if location is None:
                result.errors.append(f"{summary.external_event_key}: location missing for protocol fetch")
                continue
            summary_row = (
                db.query(EventSummary)
                .filter(
                    EventSummary.platform_id == platform.id,
                    EventSummary.external_event_key == summary.external_event_key,
                )
                .one()
            )
            try:
                upsert_result = fetch_and_upsert_activity_protocol(
                    db,
                    platform,
                    location,
                    summary,
                    summary_row,
                )
                result.run_results_upserted += upsert_result.run_results_upserted
                result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
                result.protocols_fetched += 1
            except Exception as exc:
                result.errors.append(f"{summary.external_event_key}: {exc}")
                summary_row.sync_status = SyncStatus.error
                summary_row.error_message = str(exc)

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.summaries_total,
            upserted=result.summaries_upserted + result.protocols_fetched,
            unchanged=result.unchanged,
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform)
        _finish_sync_run(db, failed_run, success=False, fetched=0, upserted=0, unchanged=0, error=str(exc))
        db.commit()
        raise
