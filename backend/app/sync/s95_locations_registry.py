from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.s95.api_client import S95ApiLocation, fetch_all_locations
from app.services.location_geo_service import apply_reverse_geocode_to_location
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step
from app.sync.location_registry_status import apply_location_registry_flags

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"


@dataclass
class S95LocationRegistrySyncOptions:
    limit: int | None = None


@dataclass
class S95LocationRegistrySyncResult:
    entries_total: int = 0
    locations_updated: int = 0
    locations_created: int = 0
    regions_backfilled: int = 0
    cancel_status_changed: int = 0
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="s95:locations_registry",
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


def _to_canonical(entry: S95ApiLocation) -> CanonicalLocation:
    return CanonicalLocation(
        external_key=entry.slug,
        name=entry.name,
        city=entry.town or None,
        latitude=entry.latitude,
        longitude=entry.longitude,
        source_url=f"{entry.domain}/events/{entry.slug}",
    )


def _process_entry(
    db: Session,
    platform: Platform,
    entry: S95ApiLocation,
    result: S95LocationRegistrySyncResult,
) -> None:
    is_cancelled = not entry.active

    row = (
        db.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key == entry.slug)
        .one_or_none()
    )

    if row is None:
        canonical = _to_canonical(entry)
        row, _ = upsert.upsert_location(db, platform, canonical)
        if entry.latitude is not None:
            row.is_official_map = True
        _, _, cancel_changed = apply_location_registry_flags(row, is_paused=False, is_cancelled=is_cancelled)
        if cancel_changed:
            result.cancel_status_changed += 1
        if apply_reverse_geocode_to_location(row):
            result.regions_backfilled += 1
        result.locations_created += 1
        db.flush()
        return

    changed = False

    # Update name / source_url if changed
    source_url = f"{entry.domain}/events/{entry.slug}"
    if row.name != entry.name:
        row.name = entry.name
        changed = True
    if row.source_url != source_url:
        row.source_url = source_url
        changed = True

    # Update coordinates if API now has them and we don't
    if entry.latitude is not None and (row.latitude is None or row.longitude is None):
        row.latitude = entry.latitude
        row.longitude = entry.longitude
        row.is_official_map = True
        changed = True

    # Update coordinates if they changed (API is authoritative)
    if (
        entry.latitude is not None
        and row.latitude is not None
        and (abs(row.latitude - entry.latitude) > 1e-4 or abs(row.longitude - (entry.longitude or 0)) > 1e-4)
    ):
        row.latitude = entry.latitude
        row.longitude = entry.longitude
        changed = True

    _, _, cancel_changed = apply_location_registry_flags(row, is_paused=False, is_cancelled=is_cancelled)
    if cancel_changed:
        result.cancel_status_changed += 1
        changed = True

    if changed:
        row.fetched_at = datetime.now(timezone.utc)
        result.locations_updated += 1
        db.flush()

    if apply_reverse_geocode_to_location(row):
        result.regions_backfilled += 1
        result.locations_updated += 1
        db.flush()


def sync_s95_locations_registry(
    db: Session,
    options: S95LocationRegistrySyncOptions | None = None,
) -> S95LocationRegistrySyncResult:
    options = options or S95LocationRegistrySyncOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95LocationRegistrySyncResult()
    sync_run = _start_sync_run(db, platform)
    db.commit()

    try:
        entries = fetch_all_locations()
        if options.limit is not None:
            entries = entries[: options.limit]

        result.entries_total = len(entries)
        for entry in entries:
            try:
                _process_entry(db, platform, entry, result)
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{entry.slug}: {exc}")
                logger.warning("S95 location sync error for %s: %s", entry.slug, exc, exc_info=True)

        unchanged = max(
            0,
            result.entries_total
            - result.locations_created
            - result.locations_updated
            - len(result.errors),
        )
        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.entries_total,
            upserted=result.locations_created + result.locations_updated,
            unchanged=unchanged,
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
