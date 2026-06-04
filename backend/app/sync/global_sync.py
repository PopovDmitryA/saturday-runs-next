from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import EventSummary, Platform, SyncRun, SyncRunStatus, SyncStatus
from app.platform_adapters.five_verst import bulk_parser
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol

PLATFORM_CODE = "five_verst"


@dataclass
class LocationSyncOptions:
    location_slug: str
    summaries_limit: int | None = None
    protocol_fetch_limit: int | None = 1
    fetch_all_protocols_on_change: bool = True


@dataclass
class LocationSyncResult:
    location_slug: str
    location_upserted: bool = False
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


def _select_summaries_for_protocol_fetch(
    summaries_to_fetch: list[tuple[EventSummary, object]],
    *,
    protocol_fetch_limit: int | None,
    fetch_all_protocols_on_change: bool,
) -> list[tuple[EventSummary, object]]:
    if protocol_fetch_limit == 0:
        return []
    if fetch_all_protocols_on_change or protocol_fetch_limit is None:
        return summaries_to_fetch
    return summaries_to_fetch[:protocol_fetch_limit]


def sync_location(db: Session, options: LocationSyncOptions) -> LocationSyncResult:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = LocationSyncResult(location_slug=options.location_slug)
    sync_run = _start_sync_run(db, platform, f"five_verst:location:{options.location_slug}")

    try:
        location_data, location_html = bulk_parser.fetch_location(options.location_slug)
        location_row, location_changed = upsert.upsert_location(
            db,
            platform,
            location_data,
            source_hash=bulk_parser.source_hash(location_html),
        )
        result.location_upserted = location_changed

        summaries, _ = bulk_parser.fetch_event_summaries(
            options.location_slug,
            location_data.name,
            limit=options.summaries_limit,
        )
        result.summaries_total = len(summaries)

        summaries_to_fetch: list[tuple[EventSummary, object]] = []
        for summary in summaries:
            summary_row, changed = upsert.upsert_event_summary(db, platform, location_row, summary)
            if changed:
                result.summaries_upserted += 1
                summaries_to_fetch.append((summary_row, summary))
            elif summary_row.event_id is None:
                summaries_to_fetch.append((summary_row, summary))
            else:
                result.summaries_unchanged += 1

        protocol_queue = _select_summaries_for_protocol_fetch(
            summaries_to_fetch,
            protocol_fetch_limit=options.protocol_fetch_limit,
            fetch_all_protocols_on_change=options.fetch_all_protocols_on_change,
        )

        for summary_row, summary in protocol_queue:
            try:
                upsert_result = fetch_and_upsert_event_protocol(
                    db,
                    platform,
                    location_row,
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

        sync_run.records_fetched = result.summaries_total
        sync_run.records_upserted = result.summaries_upserted + result.protocols_fetched
        sync_run.records_unchanged = result.summaries_unchanged
        _finish_sync_run(db, sync_run, success=not result.errors, error="; ".join(result.errors) or None)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform, f"five_verst:location:{options.location_slug}")
        _finish_sync_run(db, failed_run, success=False, error=str(exc))
        db.commit()
        raise


def sync_location_summaries_only(db: Session, location_slug: str, summaries_limit: int | None = None) -> LocationSyncResult:
    return sync_location(
        db,
        LocationSyncOptions(
            location_slug=location_slug,
            summaries_limit=summaries_limit,
            protocol_fetch_limit=0,
        ),
    )
