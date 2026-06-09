from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EventSummary, Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.s95.fetch import fetch_page_html
from app.s95.parsers.summary import parse_location_summary_html
from app.sync import upsert
from app.sync.iteration_commit import commit_step, persist_summary_error, rollback_step
from app.sync.s95_protocol import fetch_and_upsert_activity_protocol
from app.sync.s95_summary_plan import SummarySyncAction, classify_event_summary, plan_protocol_queue

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"


@dataclass
class S95LocationSyncOptions:
    location_external_key: str
    location_name: str
    location_source_url: str
    summaries_limit: int | None = None
    protocol_fetch_limit: int = 3
    fetch_all_protocols_on_change: bool = True


@dataclass
class S95LocationSyncResult:
    location_external_key: str
    summaries_total: int = 0
    summaries_upserted: int = 0
    summaries_unchanged: int = 0
    protocols_fetched: int = 0
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


def _ensure_location_row(
    db: Session,
    platform: Platform,
    options: S95LocationSyncOptions,
) -> Location:
    row = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == options.location_external_key,
        )
        .one_or_none()
    )
    if row is not None:
        return row

    canonical = CanonicalLocation(
        external_key=options.location_external_key,
        name=options.location_name,
        source_url=options.location_source_url,
    )
    row, _ = upsert.upsert_location(db, platform, canonical)
    return row


def sync_s95_location(db: Session, options: S95LocationSyncOptions) -> S95LocationSyncResult:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95LocationSyncResult(location_external_key=options.location_external_key)
    sync_run = _start_sync_run(db, platform, f"s95:location:{options.location_external_key}")
    db.commit()

    try:
        location_row = _ensure_location_row(db, platform, options)
        html = fetch_page_html(options.location_source_url, reason="location_summary")
        source_hash = hashlib.sha256(html.encode()).hexdigest()[:32]

        summaries = parse_location_summary_html(
            html,
            options.location_source_url,
            location_external_key=options.location_external_key,
            location_name=options.location_name,
        )
        if options.summaries_limit is not None:
            summaries = summaries[: options.summaries_limit]

        result.summaries_total = len(summaries)
        apply_items = []
        for summary in summaries:
            try:
                plan_item = classify_event_summary(db, platform, summary)
                summary_row, changed = upsert.upsert_event_summary(db, platform, location_row, summary)
                if changed or plan_item.action != SummarySyncAction.unchanged:
                    result.summaries_upserted += 1
                elif plan_item.action == SummarySyncAction.unchanged:
                    result.summaries_unchanged += 1
                if plan_item.action != SummarySyncAction.unchanged:
                    apply_items.append(plan_item)
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{summary.external_event_key}: {exc}")

        protocol_queue = plan_protocol_queue(
            apply_items,
            protocol_fetch_limit=options.protocol_fetch_limit,
            fetch_all_protocols_on_change=options.fetch_all_protocols_on_change,
        )

        for item in protocol_queue:
            summary = item.summary
            summary_row = (
                db.query(EventSummary)
                .filter(
                    EventSummary.platform_id == platform.id,
                    EventSummary.external_event_key == summary.external_event_key,
                )
                .one()
            )
            if not summary.source_url:
                continue
            try:
                upsert_result = fetch_and_upsert_activity_protocol(
                    db,
                    platform,
                    location_row,
                    summary,
                    summary_row,
                )
                result.run_results_upserted += upsert_result.run_results_upserted
                result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
                result.protocols_fetched += 1
                commit_step(db)
            except Exception as exc:
                result.errors.append(f"{summary.external_event_key}: {exc}")
                persist_summary_error(
                    db,
                    platform_id=platform.id,
                    external_event_key=summary.external_event_key,
                    message=str(exc),
                )

        location_row.source_hash = source_hash
        location_row.fetched_at = datetime.now(timezone.utc)
        db.flush()
        commit_step(db)

        sync_run.records_fetched = result.summaries_total
        sync_run.records_upserted = result.summaries_upserted + result.protocols_fetched
        sync_run.records_unchanged = result.summaries_unchanged
        _finish_sync_run(db, sync_run, success=not result.errors, error="; ".join(result.errors) or None)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform, f"s95:location:{options.location_external_key}")
        _finish_sync_run(db, failed_run, success=False, error=str(exc))
        db.commit()
        raise


def configured_s95_locations() -> list[S95LocationSyncOptions]:
    from app.sync.s95_location_sync import LOCATION_POOL

    settings = get_settings()
    raw = settings.s95_global_sync_locations.strip()
    if not raw:
        return [
            S95LocationSyncOptions(
                location_external_key=key,
                location_name=name,
                location_source_url=f"https://s95.ru/events/{key}",
                protocol_fetch_limit=settings.s95_global_sync_protocol_limit,
            )
            for key, name in LOCATION_POOL
        ]

    locations: list[S95LocationSyncOptions] = []
    for chunk in raw.split(";"):
        part = chunk.strip()
        if not part:
            continue
        # key|Name|https://s95.ru/events/slug/
        pieces = [p.strip() for p in part.split("|")]
        if len(pieces) == 2:
            key, name = pieces
            url = f"https://s95.ru/events/{key}"
        elif len(pieces) == 3:
            key, name, url = pieces
        else:
            logger.warning("Skip invalid s95_global_sync_locations entry: %s", part)
            continue
        locations.append(
            S95LocationSyncOptions(
                location_external_key=key,
                location_name=name,
                location_source_url=url,
                protocol_fetch_limit=settings.s95_global_sync_protocol_limit,
            )
        )
    return locations
