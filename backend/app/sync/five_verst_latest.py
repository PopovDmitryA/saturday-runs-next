from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.orm import Session

from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.protocol_pause import wait_between_protocols
from app.models import EventSummary, Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation
from app.platform_adapters.five_verst import bulk_parser
from app.services.sync_report_labels import protocol_detail_label
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
from app.sync.iteration_commit import commit_step, persist_summary_error, rollback_step

PLATFORM_CODE = "five_verst"
logger = logging.getLogger(__name__)


class LatestResultAction(str, Enum):
    unchanged = "unchanged"
    new_summary = "new_summary"
    changed_summary = "changed_summary"
    missing_protocol = "missing_protocol"


@dataclass(frozen=True)
class LatestResultPlanItem:
    summary: CanonicalEventSummary
    action: LatestResultAction
    location_id: str | None = None


@dataclass
class LatestResultsSyncOptions:
    dry_run: bool = False
    update_limit: int | None = None
    protocol_fetch_limit: int | None = None
    ensure_locations: bool = True
    fetch_all_protocols_on_change: bool = True


@dataclass
class LatestResultsSyncResult:
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
    fetched_protocols: list[str] = field(default_factory=list)
    changed_protocols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="five_verst:latest",
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


def _classify_summary(db: Session, platform: Platform, summary: CanonicalEventSummary) -> LatestResultPlanItem:
    row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.external_event_key == summary.external_event_key,
        )
        .one_or_none()
    )
    if row is None:
        return LatestResultPlanItem(summary=summary, action=LatestResultAction.new_summary)
    if row.summary_hash != summary.summary_hash:
        return LatestResultPlanItem(
            summary=summary,
            action=LatestResultAction.changed_summary,
            location_id=str(row.location_id),
        )
    if row.event_id is None:
        return LatestResultPlanItem(
            summary=summary,
            action=LatestResultAction.missing_protocol,
            location_id=str(row.location_id),
        )
    return LatestResultPlanItem(
        summary=summary,
        action=LatestResultAction.unchanged,
        location_id=str(row.location_id),
    )


def plan_latest_results_sync(db: Session, summaries: list[CanonicalEventSummary]) -> list[LatestResultPlanItem]:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    return [_classify_summary(db, platform, summary) for summary in summaries]


def _ensure_location(
    db: Session,
    platform: Platform,
    summary: CanonicalEventSummary,
    *,
    result: LatestResultsSyncResult,
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
            row.source_url = bulk_parser._location_url(summary.location_external_key)
            db.flush()
        return row

    try:
        location_data, location_html = bulk_parser.fetch_location(summary.location_external_key)
    except Exception as exc:
        result.errors.append(f"{summary.location_external_key}: location fetch failed: {exc}")
        result.locations_missing += 1
        return None

    location_data = CanonicalLocation(
        external_key=location_data.external_key,
        name=summary.location_name or location_data.name,
        country=location_data.country,
        city=location_data.city,
        region=location_data.region,
        latitude=location_data.latitude,
        longitude=location_data.longitude,
        source_url=location_data.source_url,
        course_source_url=location_data.course_source_url,
        map_url=location_data.map_url,
    )
    row, _ = upsert.upsert_location(
        db,
        platform,
        location_data,
        source_hash=bulk_parser.source_hash(location_html),
    )
    if location_data.latitude is not None and location_data.longitude is not None:
        row.is_official_map = True
    result.locations_created += 1
    db.flush()
    return row


def _plan_protocol_queue(
    apply_items: list[LatestResultPlanItem],
    *,
    protocol_fetch_limit: int | None,
    fetch_all_protocols_on_change: bool,
) -> list[LatestResultPlanItem]:
    del fetch_all_protocols_on_change  # порядок очереди от флага не зависит
    priority = [
        item
        for item in apply_items
        if item.action in (LatestResultAction.changed_summary, LatestResultAction.missing_protocol)
    ]
    regular = [item for item in apply_items if item.action == LatestResultAction.new_summary]
    combined = priority + regular
    if protocol_fetch_limit is None:
        return combined
    if protocol_fetch_limit <= 0:
        return []
    return combined[:protocol_fetch_limit]


def _fetch_protocol_for_summary(
    db: Session,
    platform: Platform,
    location: Location,
    summary: CanonicalEventSummary,
    summary_row: EventSummary,
    *,
    result: LatestResultsSyncResult,
) -> None:
    upsert_result = fetch_and_upsert_event_protocol(
        db,
        platform,
        location,
        summary,
        summary_row,
    )
    result.run_results_upserted += upsert_result.run_results_upserted
    result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
    result.protocols_fetched += 1
    label = protocol_detail_label(location.external_key, summary.external_event_key, location.name)
    result.fetched_protocols.append(label)
    if upsert_result.protocol_changed:
        result.changed_protocols.append(label)


def sync_latest_results(
    db: Session,
    options: LatestResultsSyncOptions | None = None,
) -> LatestResultsSyncResult:
    options = options or LatestResultsSyncOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = LatestResultsSyncResult()

    summaries, _ = bulk_parser.fetch_latest_results()
    result.summaries_total = len(summaries)

    plan = plan_latest_results_sync(db, summaries)
    to_update: list[LatestResultPlanItem] = []

    for item in plan:
        if item.action == LatestResultAction.unchanged:
            result.unchanged += 1
            continue
        if item.action == LatestResultAction.new_summary:
            result.new_summaries += 1
            to_update.append(item)
        elif item.action == LatestResultAction.changed_summary:
            result.changed_summaries += 1
            to_update.append(item)
        elif item.action == LatestResultAction.missing_protocol:
            result.missing_protocol += 1
            to_update.append(item)

    result.needs_update = len(to_update)
    logger.info(
        "latest sync: plan unchanged=%d new=%d changed=%d missing_protocol=%d",
        result.unchanged,
        result.new_summaries,
        result.changed_summaries,
        result.missing_protocol,
    )
    apply_items = to_update
    if options.update_limit is not None:
        apply_items = to_update[: options.update_limit]
    result.planned_protocols = [
        item.summary.external_event_key
        for item in _plan_protocol_queue(
            apply_items,
            protocol_fetch_limit=options.protocol_fetch_limit,
            fetch_all_protocols_on_change=options.fetch_all_protocols_on_change,
        )
    ]

    if options.dry_run:
        return result

    sync_run = _start_sync_run(db, platform)
    db.commit()
    protocol_queue = _plan_protocol_queue(
        apply_items,
        protocol_fetch_limit=options.protocol_fetch_limit,
        fetch_all_protocols_on_change=options.fetch_all_protocols_on_change,
    )

    try:
        for item in apply_items:
            summary = item.summary
            try:
                location: Location | None = None
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

                summary_row, changed = upsert.upsert_event_summary(db, platform, location, summary)
                if changed or item.action != LatestResultAction.unchanged:
                    result.summaries_upserted += 1
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{summary.external_event_key}: {exc}")

        logger.info("latest sync: fetching %d protocols", len(protocol_queue))
        for index, item in enumerate(protocol_queue):
            summary = item.summary
            logger.info(
                "latest sync: protocol %d/%d %s (%s)",
                index + 1,
                len(protocol_queue),
                summary.location_external_key,
                summary.external_event_key,
            )
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
                _fetch_protocol_for_summary(
                    db,
                    platform,
                    location,
                    summary,
                    summary_row,
                    result=result,
                )
                if index + 1 < len(protocol_queue):
                    wait_between_protocols(reason="latest")
                commit_step(db)
            except FiveVerstBanDetected as exc:
                # Кулдаун общий для всех фетчей — остаток очереди упал бы с той же
                # ошибкой. Недокачанные протоколы заберёт следующий запуск.
                result.errors.append(f"{summary.external_event_key}: {exc}; остаток очереди отложен")
                break
            except Exception as exc:
                result.errors.append(f"{summary.external_event_key}: {exc}")
                persist_summary_error(
                    db,
                    platform_id=platform.id,
                    external_event_key=summary.external_event_key,
                    message=str(exc),
                )

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
        # sync_run закоммичен до цикла — закрываем его, а не создаём второй
        # failed-ран, оставляя первый в running навсегда.
        _finish_sync_run(db, sync_run, success=False, fetched=0, upserted=0, unchanged=0, error=str(exc))
        db.commit()
        raise
