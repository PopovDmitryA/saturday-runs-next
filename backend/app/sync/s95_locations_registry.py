from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.geo.reverse_geocode import lookup_address
from app.models import Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.s95.fetch import fetch_page_html
from app.s95.parsers.events_registry import (
    S95RegistryEntry,
    parse_events_registry_html,
    registry_entry_is_cancelled,
    registry_entry_is_paused,
)
from app.s95.parsers.location import parse_event_location_page, parse_location_coordinates, parse_map_url
from app.services.location_geo_service import apply_reverse_geocode_to_location
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step
from app.sync.location_registry_status import apply_location_registry_flags

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"
EVENTS_PAGE_URL = "https://s95.ru/events"


@dataclass
class S95LocationRegistrySyncOptions:
    fetch_new_location_details: bool = True
    fetch_missing_coordinates: bool = True
    limit: int | None = None


@dataclass
class S95LocationRegistrySyncResult:
    entries_total: int = 0
    locations_updated: int = 0
    locations_created: int = 0
    locations_skipped_no_coords: int = 0
    coords_fetched: int = 0
    regions_backfilled: int = 0
    pause_status_changed: int = 0
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


def _enrich_location_geo(location: CanonicalLocation) -> CanonicalLocation:
    if location.latitude is None or location.longitude is None:
        return location
    try:
        address = lookup_address(location.latitude, location.longitude)
    except Exception:
        logger.warning("Reverse geocode failed for %s", location.external_key, exc_info=True)
        return location
    return CanonicalLocation(
        external_key=location.external_key,
        name=location.name,
        country=address.get("country") or location.country,
        city=address.get("city") or location.city,
        region=address.get("region") or location.region,
        latitude=location.latitude,
        longitude=location.longitude,
        source_url=location.source_url,
        course_source_url=location.course_source_url,
        map_url=location.map_url,
    )


def _apply_registry_meta(db: Session, row: Location, entry: S95RegistryEntry) -> tuple[bool, bool, bool]:
    changed, pause_changed, cancel_changed = apply_location_registry_flags(
        row,
        is_paused=registry_entry_is_paused(entry.status),
        is_cancelled=registry_entry_is_cancelled(entry.status),
    )
    name_changed = False
    if row.name != entry.name:
        row.name = entry.name
        name_changed = True
    if row.source_url != entry.source_url:
        row.source_url = entry.source_url
        name_changed = True
    if name_changed and not changed:
        row.fetched_at = datetime.now(timezone.utc)
        changed = True
    if changed:
        db.flush()
    return changed, pause_changed, cancel_changed


def _fetch_location_detail(entry: S95RegistryEntry) -> tuple[CanonicalLocation, str]:
    map_html = fetch_page_html(
        entry.source_url,
        reason="location_map",
        extra_wait_ms=8000,
        click_map_tab=True,
    )
    canonical = parse_event_location_page(
        map_html,
        entry.source_url,
        location_external_key=entry.slug,
    )
    canonical.name = entry.name or canonical.name
    lat, lon = parse_location_coordinates(map_html)
    if lat is not None:
        canonical.latitude = lat
        canonical.longitude = lon
    if canonical.map_url is None:
        canonical.map_url = parse_map_url(map_html)
    return canonical, map_html


def _process_registry_entry(
    db: Session,
    platform: Platform,
    entry: S95RegistryEntry,
    *,
    fetch_new_location_details: bool,
    fetch_missing_coordinates: bool,
    result: S95LocationRegistrySyncResult,
) -> None:
    row = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == entry.slug,
        )
        .one_or_none()
    )

    if row is not None:
        meta_changed, pause_changed, cancel_changed = _apply_registry_meta(db, row, entry)
        if row.latitude is not None and row.longitude is not None and not row.is_official_map:
            row.is_official_map = True
            meta_changed = True
        if meta_changed:
            result.locations_updated += 1
        if pause_changed:
            result.pause_status_changed += 1
        if cancel_changed:
            result.cancel_status_changed += 1

        needs_coords = row.latitude is None or row.longitude is None
        if needs_coords and fetch_missing_coordinates:
            try:
                location_data, location_html = _fetch_location_detail(entry)
                if location_data.latitude is None or location_data.longitude is None:
                    result.locations_skipped_no_coords += 1
                else:
                    location_data = _enrich_location_geo(location_data)
                    updated_row, _ = upsert.upsert_location(
                        db,
                        platform,
                        location_data,
                        source_hash=hashlib.sha256(location_html.encode()).hexdigest()[:32],
                    )
                    updated_row.is_official_map = True
                    result.coords_fetched += 1
                    meta_changed, pause_changed, cancel_changed = _apply_registry_meta(db, updated_row, entry)
                    if meta_changed:
                        result.locations_updated += 1
                    if pause_changed:
                        result.pause_status_changed += 1
                    if cancel_changed:
                        result.cancel_status_changed += 1
                    row = updated_row
            except Exception as exc:
                result.errors.append(f"{entry.slug}: coords fetch failed: {exc}")

        if apply_reverse_geocode_to_location(row):
            result.regions_backfilled += 1
            result.locations_updated += 1
            db.flush()
        return

    if not fetch_new_location_details:
        return

    try:
        location_data, location_html = _fetch_location_detail(entry)
    except Exception as exc:
        result.errors.append(f"{entry.slug}: fetch failed: {exc}")
        return

    if location_data.latitude is None or location_data.longitude is None:
        logger.info("Skip new S95 location %s: no coordinates on event page", entry.slug)
        result.locations_skipped_no_coords += 1
        return

    location_data = _enrich_location_geo(location_data)
    new_row, _ = upsert.upsert_location(
        db,
        platform,
        location_data,
        source_hash=hashlib.sha256(location_html.encode()).hexdigest()[:32],
    )
    new_row.is_official_map = True
    _, pause_changed, cancel_changed = _apply_registry_meta(db, new_row, entry)
    if pause_changed:
        result.pause_status_changed += 1
    if cancel_changed:
        result.cancel_status_changed += 1
    result.locations_created += 1


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
        html = fetch_page_html(EVENTS_PAGE_URL, reason="events_registry")
        page = parse_events_registry_html(html, EVENTS_PAGE_URL)
        entries = page.entries
        if options.limit is not None:
            entries = entries[: options.limit]

        result.entries_total = len(entries)
        for entry in entries:
            try:
                _process_registry_entry(
                    db,
                    platform,
                    entry,
                    fetch_new_location_details=options.fetch_new_location_details,
                    fetch_missing_coordinates=options.fetch_missing_coordinates,
                    result=result,
                )
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{entry.slug}: {exc}")

        unchanged = max(
            0,
            result.entries_total
            - result.locations_created
            - result.locations_updated
            - result.locations_skipped_no_coords
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
