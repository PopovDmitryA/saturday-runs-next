from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.protocol_pause import wait_between_protocols
from app.models import EventSummary, Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.five_verst import bulk_parser
from app.services.sync_report_labels import protocol_detail_label
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
from app.sync.iteration_commit import commit_step, persist_summary_error, rollback_step

PLATFORM_CODE = "five_verst"
logger = logging.getLogger(__name__)


@dataclass
class LocationSyncOptions:
    location_slug: str
    summaries_limit: int | None = None
    protocol_fetch_limit: int | None = None
    fetch_all_protocols_on_change: bool = True
    # Не перекачивать страницу локации и /course/, если локация уже в базе с
    # координатами: имя и статусы ежедневно освежает синк реестра /events/.
    # Экономит 2 из 3 HTTP-страниц на каждый прогон ротации.
    skip_location_refresh: bool = False


@dataclass
class LocationSyncResult:
    location_slug: str
    location_upserted: bool = False
    summaries_total: int = 0
    summaries_upserted: int = 0
    summaries_unchanged: int = 0
    protocols_fetched: int = 0
    fetched_protocols: list[str] = field(default_factory=list)
    changed_protocols: list[str] = field(default_factory=list)
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
    db.commit()

    try:
        location_row = None
        if options.skip_location_refresh:
            location_row = (
                db.query(Location)
                .filter(
                    Location.platform_id == platform.id,
                    Location.external_key == options.location_slug,
                )
                .one_or_none()
            )
            if location_row is not None and (location_row.latitude is None or location_row.longitude is None):
                # Координат ещё нет — страница локации всё же нужна.
                location_row = None

        if location_row is None:
            logger.info("location sync: %s — fetch location page", options.location_slug)
            location_data, location_html = bulk_parser.fetch_location(options.location_slug)
            logger.info("location sync: %s — upsert location to DB", options.location_slug)
            location_row, location_changed = upsert.upsert_location(
                db,
                platform,
                location_data,
                source_hash=bulk_parser.source_hash(location_html),
            )
            result.location_upserted = location_changed
            commit_step(db)
            location_name = location_data.name
        else:
            location_name = location_row.name

        logger.info("location sync: %s — fetch event summaries", options.location_slug)
        summaries, _ = bulk_parser.fetch_event_summaries(
            options.location_slug,
            location_name,
            limit=options.summaries_limit,
        )
        result.summaries_total = len(summaries)

        summaries_to_fetch: list[tuple[EventSummary, object]] = []
        for summary in summaries:
            try:
                summary_row, changed = upsert.upsert_event_summary(db, platform, location_row, summary)
                if changed:
                    result.summaries_upserted += 1
                    summaries_to_fetch.append((summary_row, summary))
                elif summary_row.event_id is None:
                    summaries_to_fetch.append((summary_row, summary))
                else:
                    result.summaries_unchanged += 1
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{summary.external_event_key}: {exc}")

        protocol_queue = _select_summaries_for_protocol_fetch(
            summaries_to_fetch,
            protocol_fetch_limit=options.protocol_fetch_limit,
            fetch_all_protocols_on_change=options.fetch_all_protocols_on_change,
        )
        logger.info(
            "location sync: %s — %d summaries, fetching %d protocols",
            options.location_slug,
            len(summaries),
            len(protocol_queue),
        )

        for index, (summary_row, summary) in enumerate(protocol_queue):
            logger.info(
                "location sync: %s protocol %d/%d %s",
                options.location_slug,
                index + 1,
                len(protocol_queue),
                summary.external_event_key,
            )
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
                label = protocol_detail_label(
                    location_row.external_key,
                    summary.external_event_key,
                    location_row.name,
                )
                result.fetched_protocols.append(label)
                if upsert_result.protocol_changed:
                    result.changed_protocols.append(label)
                if index + 1 < len(protocol_queue):
                    wait_between_protocols(reason="location")
                commit_step(db)
            except FiveVerstBanDetected as exc:
                # Кулдаун общий для всех фетчей — остаток очереди упал бы с той
                # же ошибкой; недокачанное заберёт следующий прогон.
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

        sync_run.records_fetched = result.summaries_total
        sync_run.records_upserted = result.summaries_upserted + result.protocols_fetched
        sync_run.records_unchanged = result.summaries_unchanged
        _finish_sync_run(db, sync_run, success=not result.errors, error="; ".join(result.errors) or None)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        # Закрываем исходный (закоммиченный) ран, а не плодим второй failed,
        # оставляя первый висеть в running навсегда.
        _finish_sync_run(db, sync_run, success=False, error=str(exc))
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
