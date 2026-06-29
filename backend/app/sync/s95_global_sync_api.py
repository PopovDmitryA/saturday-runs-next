from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.s95.api_client import S95ApiLocation, fetch_all_locations, fetch_event_activities
from app.services.sync_watermark_service import (
    S95_PROTOCOLS_RECONCILED_THROUGH,
    set_watermark,
)
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step
from app.sync.s95_protocol_api import upsert_activity_protocol_api

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"

# Gentle pacing between protocol fetches. IPs are whitelisted, but we avoid hammering.
DEFAULT_REQUEST_DELAY_SEC = 0.5
BACKFILL_REQUEST_DELAY_SEC = 1.0


def most_recent_saturday(today: date) -> date:
    """The Saturday on or before `today` (Saturday == weekday 5)."""
    return today - timedelta(days=(today.weekday() - 5) % 7)


@dataclass
class S95ApiSyncResult:
    locations_total: int = 0
    protocols_created: int = 0
    protocols_updated: int = 0
    protocols_unchanged: int = 0
    errors: list[str] = field(default_factory=list)


def _start_run(db: Session, platform: Platform, sync_type: str) -> SyncRun:
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


def _finish_run(db: Session, run: SyncRun, result: S95ApiSyncResult) -> None:
    run.status = SyncRunStatus.success if not result.errors else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = result.protocols_created + result.protocols_updated + result.protocols_unchanged
    run.records_upserted = result.protocols_created + result.protocols_updated
    run.records_unchanged = result.protocols_unchanged
    run.error_message = "; ".join(result.errors[:20]) or None
    db.flush()


def _ensure_location(db: Session, platform: Platform, api_loc: S95ApiLocation) -> Location:
    row = (
        db.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key == api_loc.slug)
        .one_or_none()
    )
    if row is not None:
        return row
    canonical = CanonicalLocation(
        external_key=api_loc.slug,
        name=api_loc.name,
        city=api_loc.town or None,
        latitude=api_loc.latitude,
        longitude=api_loc.longitude,
        source_url=f"{api_loc.domain}/events/{api_loc.slug}",
    )
    row, _ = upsert.upsert_location(db, platform, canonical)
    return row


def _existing_event_keys(db: Session, platform: Platform, location: Location) -> set[str]:
    rows = (
        db.query(Event.external_event_key)
        .filter(Event.platform_id == platform.id, Event.location_id == location.id)
        .all()
    )
    return {r[0] for r in rows}


def _apply_protocol(
    db: Session,
    platform: Platform,
    location: Location,
    ref,
    result: S95ApiSyncResult,
    *,
    recalculate_pr: bool = True,
) -> None:
    try:
        outcome = upsert_activity_protocol_api(
            db, platform, location, ref, recalculate_pr=recalculate_pr
        )
        commit_step(db)  # immediate per-protocol commit — progress survives a crash
        if not outcome.changed:
            result.protocols_unchanged += 1
        elif outcome.created:
            result.protocols_created += 1
        else:
            result.protocols_updated += 1
    except Exception as exc:
        rollback_step(db)
        result.errors.append(f"{location.external_key} {ref.date}: {exc}")
        logger.warning("S95 protocol sync failed: %s %s: %s", location.external_key, ref.date, exc, exc_info=True)


def reset_s95_protocol_check_dates(db: Session, platform: Platform) -> int:
    """Clear last_protocol_check_at / last_protocol_fetched_at on every S95 protocol so a
    fresh backfill can be resumed: any protocol that already has a check date again was
    processed in the current run and can be skipped."""
    from app.models import Event, ProtocolSyncState

    event_ids = db.query(Event.id).filter(Event.platform_id == platform.id).subquery()
    rows = (
        db.query(ProtocolSyncState)
        .filter(ProtocolSyncState.event_id.in_(event_ids))
        .all()
    )
    for row in rows:
        row.last_protocol_check_at = None
        row.last_protocol_fetched_at = None
    db.commit()
    return len(rows)


def _already_checked(db: Session, platform: Platform, location: Location, ref) -> bool:
    from app.models import Event, ProtocolSyncState

    key = f"{location.external_key}:{ref.date}"
    state = (
        db.query(ProtocolSyncState)
        .join(Event, ProtocolSyncState.event_id == Event.id)
        .filter(Event.platform_id == platform.id, Event.external_event_key == key)
        .one_or_none()
    )
    return state is not None and state.last_protocol_check_at is not None


def sync_new_protocols(db: Session, *, request_delay_sec: float = DEFAULT_REQUEST_DELAY_SEC) -> S95ApiSyncResult:
    """Find and import protocols that are not yet in our DB. Cheap: skips known events
    without fetching their protocol body."""
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95ApiSyncResult()
    run = _start_run(db, platform, "s95:api:new_protocols")
    db.commit()
    try:
        locations = fetch_all_locations()
        result.locations_total = len(locations)
        for api_loc in locations:
            try:
                location = _ensure_location(db, platform, api_loc)
                commit_step(db)
                event_url = f"{api_loc.domain}/events/{api_loc.slug}.json"
                refs = fetch_event_activities(event_url)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{api_loc.slug}: list fetch failed: {exc}")
                continue

            existing = _existing_event_keys(db, platform, location)
            for ref in refs:
                key = f"{api_loc.slug}:{ref.date}"
                if key in existing:
                    continue
                _apply_protocol(db, platform, location, ref, result)
                if request_delay_sec:
                    time.sleep(request_delay_sec)
        _finish_run(db, run, result)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed = _start_run(db, platform, "s95:api:new_protocols")
        failed.status = SyncRunStatus.failed
        failed.finished_at = datetime.now(timezone.utc)
        failed.error_message = str(exc)
        db.commit()
        raise


def reconcile_protocols_for_date(
    db: Session,
    target_date: date,
    *,
    request_delay_sec: float = DEFAULT_REQUEST_DELAY_SEC,
) -> S95ApiSyncResult:
    """Re-fetch every protocol dated target_date across all locations, updating any that
    changed and bumping the check timestamp on those that did not."""
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95ApiSyncResult()
    run = _start_run(db, platform, f"s95:api:reconcile:{target_date.isoformat()}")
    db.commit()
    date_str = target_date.isoformat()
    try:
        locations = fetch_all_locations()
        result.locations_total = len(locations)
        for api_loc in locations:
            try:
                location = _ensure_location(db, platform, api_loc)
                commit_step(db)
                event_url = f"{api_loc.domain}/events/{api_loc.slug}.json"
                refs = fetch_event_activities(event_url)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{api_loc.slug}: list fetch failed: {exc}")
                continue

            for ref in refs:
                if ref.date != date_str:
                    continue
                _apply_protocol(db, platform, location, ref, result)
                if request_delay_sec:
                    time.sleep(request_delay_sec)
        _finish_run(db, run, result)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed = _start_run(db, platform, f"s95:api:reconcile:{target_date.isoformat()}")
        failed.status = SyncRunStatus.failed
        failed.finished_at = datetime.now(timezone.utc)
        failed.error_message = str(exc)
        db.commit()
        raise


def full_backfill(
    db: Session,
    *,
    limit_per_location: int | None = None,
    delay_min_sec: float = 15.0,
    delay_max_sec: float = 30.0,
    resume: bool = True,
    reset_dates: bool = False,
) -> S95ApiSyncResult:
    """One-time pass over every protocol of every location.

    - Gentle randomised pacing (delay_min_sec..delay_max_sec) between protocol fetches.
    - resume=True skips protocols already given a check date in this run (set reset_dates
      first, or call reset_s95_protocol_check_dates manually, so a crash can be resumed).
    - PR is recomputed once at the end (not per protocol) for efficiency.
    - On full success records the reconciled-through watermark for future ?since= sync.
    """
    import random

    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95ApiSyncResult()
    started_at = datetime.now(timezone.utc)

    if reset_dates:
        cleared = reset_s95_protocol_check_dates(db, platform)
        logger.info("Reset protocol check dates for %d S95 protocols", cleared)

    run = _start_run(db, platform, "s95:api:full_backfill")
    db.commit()
    try:
        locations = fetch_all_locations()
        result.locations_total = len(locations)
        for api_loc in locations:
            try:
                location = _ensure_location(db, platform, api_loc)
                commit_step(db)
                event_url = f"{api_loc.domain}/events/{api_loc.slug}.json"
                refs = fetch_event_activities(event_url)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{api_loc.slug}: list fetch failed: {exc}")
                continue

            refs = sorted(refs, key=lambda r: r.date)
            if limit_per_location is not None:
                refs = refs[:limit_per_location]
            for ref in refs:
                if resume and _already_checked(db, platform, location, ref):
                    result.protocols_unchanged += 1
                    continue
                _apply_protocol(db, platform, location, ref, result, recalculate_pr=False)
                if delay_max_sec > 0:
                    time.sleep(random.uniform(delay_min_sec, delay_max_sec))

        # Recompute personal records once over the whole platform after the bulk load.
        if not result.errors:
            from app.services.personal_record_service import recalculate_personal_records

            recalculate_personal_records(db, PLATFORM_CODE)
            set_watermark(
                db,
                platform,
                S95_PROTOCOLS_RECONCILED_THROUGH,
                started_at,
                note="full_backfill",
            )
        _finish_run(db, run, result)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed = _start_run(db, platform, "s95:api:full_backfill")
        failed.status = SyncRunStatus.failed
        failed.finished_at = datetime.now(timezone.utc)
        failed.error_message = str(exc)
        db.commit()
        raise
