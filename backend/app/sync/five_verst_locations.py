from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.geo.reverse_geocode import lookup_address
from app.models import Location, LocationMergeRequest, LocationMergeRequestStatus, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.bulk_parser import (
    ParsedRegistryEntry,
    registry_entry_is_cancelled,
    registry_entry_is_paused,
)
from app.platform_adapters.five_verst.http import NotFoundError
from app.services.location_geo_service import apply_reverse_geocode_to_location
from app.services.vk_admin_notify import send_vk_admin_message, vk_admin_configured
from app.sync import upsert
from app.sync.location_registry_status import apply_location_registry_flags

logger = logging.getLogger(__name__)

PLATFORM_CODE = "five_verst"
DUPLICATE_OVERLAP_THRESHOLD = 5
DUPLICATE_SAMPLE_SUMMARIES = 15


@dataclass
class LocationRegistrySyncOptions:
    fetch_missing_coordinates: bool = True
    detect_duplicates: bool = True
    limit: int | None = None


@dataclass
class LocationRegistrySyncResult:
    entries_total: int = 0
    locations_updated: int = 0
    locations_created: int = 0
    locations_skipped_no_coords: int = 0
    coords_fetched: int = 0
    regions_backfilled: int = 0
    pause_status_changed: int = 0
    cancel_status_changed: int = 0
    merge_requests_created: int = 0
    merge_notifications_sent: int = 0
    updated_locations: list[str] = field(default_factory=list)
    created_locations: list[str] = field(default_factory=list)
    coords_fetched_locations: list[str] = field(default_factory=list)
    pause_changed_locations: list[str] = field(default_factory=list)
    cancel_changed_locations: list[str] = field(default_factory=list)
    merge_request_locations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _location_label(slug: str, name: str | None = None) -> str:
    if name and name.strip() and name.strip() != slug:
        return f"{slug} ({name.strip()})"
    return slug


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="locations_registry",
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


def _backfill_geo_for_row(db: Session, row: Location) -> bool:
    changed = apply_reverse_geocode_to_location(row)
    if changed:
        db.flush()
    return changed


def _apply_registry_meta(db: Session, row: Location, entry: ParsedRegistryEntry) -> tuple[bool, bool, bool]:
    changed, pause_changed, cancel_changed = apply_location_registry_flags(
        row,
        is_paused=registry_entry_is_paused(entry.status),
        is_cancelled=registry_entry_is_cancelled(entry.status),
    )
    if row.name != entry.name:
        row.name = entry.name
        changed = True
    if row.source_url != entry.source_url:
        row.source_url = entry.source_url
        changed = True
    if changed:
        db.flush()
    return changed, pause_changed, cancel_changed


def _location_event_dates(db: Session, platform_id, location_id) -> set[date]:
    from app.models import EventSummary

    rows = (
        db.query(EventSummary.event_date)
        .filter(
            EventSummary.platform_id == platform_id,
            EventSummary.location_id == location_id,
        )
        .all()
    )
    return {row.event_date for row in rows}


def _candidate_summary_dates(slug: str, location_name: str) -> set[date]:
    try:
        summaries, _ = bulk_parser.fetch_event_summaries(
            slug,
            location_name,
            limit=DUPLICATE_SAMPLE_SUMMARIES,
        )
    except Exception:
        logger.warning("Could not fetch summaries for duplicate check: %s", slug, exc_info=True)
        return set()
    return {summary.event_date for summary in summaries}


def _find_duplicate_matches(
    db: Session,
    platform: Platform,
    candidate: ParsedRegistryEntry,
    candidate_dates: set[date],
) -> list[tuple[Location, set[date]]]:
    if len(candidate_dates) < DUPLICATE_OVERLAP_THRESHOLD:
        return []

    existing_locations = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key != candidate.slug,
        )
        .all()
    )
    matches: list[tuple[Location, set[date]]] = []
    for location in existing_locations:
        existing_dates = _location_event_dates(db, platform.id, location.id)
        overlap = candidate_dates & existing_dates
        if len(overlap) >= DUPLICATE_OVERLAP_THRESHOLD:
            matches.append((location, overlap))
    matches.sort(key=lambda item: len(item[1]), reverse=True)
    return matches


def _create_merge_request_if_needed(
    db: Session,
    platform: Platform,
    candidate: ParsedRegistryEntry,
    matched: Location,
    overlap: set[date],
) -> LocationMergeRequest | None:
    overlap_sorted = sorted(overlap)
    existing = (
        db.query(LocationMergeRequest)
        .filter(
            LocationMergeRequest.platform_id == platform.id,
            LocationMergeRequest.candidate_slug == candidate.slug,
            LocationMergeRequest.matched_location_id == matched.id,
            LocationMergeRequest.status == LocationMergeRequestStatus.pending,
        )
        .one_or_none()
    )
    if existing is not None:
        existing.overlap_count = len(overlap_sorted)
        existing.overlap_event_dates = [item.isoformat() for item in overlap_sorted]
        existing.candidate_name = candidate.name
        existing.candidate_source_url = candidate.source_url
        db.flush()
        return existing

    row = LocationMergeRequest(
        platform_id=platform.id,
        candidate_slug=candidate.slug,
        candidate_name=candidate.name,
        candidate_source_url=candidate.source_url,
        matched_location_id=matched.id,
        matched_slug=matched.external_key,
        overlap_count=len(overlap_sorted),
        overlap_event_dates=[item.isoformat() for item in overlap_sorted],
        status=LocationMergeRequestStatus.pending,
    )
    db.add(row)
    db.flush()
    return row


def _notify_merge_request(request: LocationMergeRequest) -> bool:
    if not vk_admin_configured():
        logger.info("VK admin notify not configured, skip merge notification for %s", request.candidate_slug)
        return False

    sample_dates = ", ".join(request.overlap_event_dates[:5])
    if len(request.overlap_event_dates) > 5:
        sample_dates += ", …"

    text = (
        "⚠️ Возможный дубль локации 5 вёрst\n\n"
        f"Новый slug: {request.candidate_slug}\n"
        f"Имя: {request.candidate_name}\n"
        f"URL: {request.candidate_source_url or '—'}\n\n"
        f"Похоже на: {request.matched_slug}\n"
        f"Совпадающих дат пробежек: {request.overlap_count}\n"
        f"Примеры дат: {sample_dates}\n\n"
        "Проверьте вручную: возможно, локация сменила URL/имя."
    )
    return send_vk_admin_message(text) is not None


def _friendly_fetch_error(exc: Exception) -> str:
    message = str(exc)
    if "Read timed out" in message:
        return "таймаут при загрузке страницы (сайт не ответил вовремя)"
    if "Страница не найдена" in message:
        return message
    if "Rate limit" in message or "429" in message:
        return "ограничение частоты запросов на 5verst.ru"
    return message


def _record_location(result: LocationRegistrySyncResult, field: str, slug: str, name: str | None) -> None:
    label = _location_label(slug, name)
    bucket: list[str] = getattr(result, field)
    if label not in bucket:
        bucket.append(label)


def _process_registry_entry(
    db: Session,
    platform: Platform,
    entry: ParsedRegistryEntry,
    *,
    fetch_missing_coordinates: bool,
    detect_duplicates: bool,
    result: LocationRegistrySyncResult,
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
        if meta_changed:
            result.locations_updated += 1
            _record_location(result, "updated_locations", entry.slug, entry.name)
        if pause_changed:
            result.pause_status_changed += 1
            _record_location(result, "pause_changed_locations", entry.slug, entry.name)
        if cancel_changed:
            result.cancel_status_changed += 1
            _record_location(result, "cancel_changed_locations", entry.slug, entry.name)

        needs_coords = row.latitude is None or row.longitude is None
        if needs_coords and fetch_missing_coordinates:
            try:
                location_data, location_html = bulk_parser.fetch_location(entry.slug)
                if location_data.latitude is None or location_data.longitude is None:
                    result.locations_skipped_no_coords += 1
                else:
                    location_data = _enrich_location_geo(location_data)
                    updated_row, changed = upsert.upsert_location(
                        db,
                        platform,
                        location_data,
                        source_hash=bulk_parser.source_hash(location_html),
                    )
                    updated_row.is_official_map = True
                    result.coords_fetched += 1
                    _record_location(result, "coords_fetched_locations", entry.slug, entry.name)
                    meta_changed, pause_changed, cancel_changed = _apply_registry_meta(db, updated_row, entry)
                    if meta_changed:
                        result.locations_updated += 1
                        _record_location(result, "updated_locations", entry.slug, entry.name)
                    if pause_changed:
                        result.pause_status_changed += 1
                        _record_location(result, "pause_changed_locations", entry.slug, entry.name)
                    if cancel_changed:
                        result.cancel_status_changed += 1
                        _record_location(result, "cancel_changed_locations", entry.slug, entry.name)
                    row = updated_row
            except NotFoundError:
                result.locations_skipped_no_coords += 1
            except Exception as exc:
                result.errors.append(f"{entry.slug}: координаты — {_friendly_fetch_error(exc)}")

        if _backfill_geo_for_row(db, row):
            result.regions_backfilled += 1
            result.locations_updated += 1
            _record_location(result, "updated_locations", entry.slug, entry.name)
        return

    try:
        location_data, location_html = bulk_parser.fetch_location(entry.slug)
    except NotFoundError:
        logger.info("Skip new location %s: course or home page not found", entry.slug)
        result.locations_skipped_no_coords += 1
        return
    except Exception as exc:
        result.errors.append(f"{entry.slug}: {_friendly_fetch_error(exc)}")
        return

    if location_data.latitude is None or location_data.longitude is None:
        logger.info("Skip new location %s: no coordinates on /course/", entry.slug)
        result.locations_skipped_no_coords += 1
        return

    location_data = _enrich_location_geo(location_data)
    location_data = CanonicalLocation(
        external_key=location_data.external_key,
        name=entry.name or location_data.name,
        country=location_data.country,
        city=entry.city_group or location_data.city,
        region=location_data.region,
        latitude=location_data.latitude,
        longitude=location_data.longitude,
        source_url=entry.source_url,
        course_source_url=location_data.course_source_url,
        map_url=location_data.map_url,
    )
    new_row, _ = upsert.upsert_location(
        db,
        platform,
        location_data,
        source_hash=bulk_parser.source_hash(location_html),
    )
    new_row.is_official_map = True
    _, pause_changed, cancel_changed = _apply_registry_meta(db, new_row, entry)
    if pause_changed:
        result.pause_status_changed += 1
        _record_location(result, "pause_changed_locations", entry.slug, entry.name)
    if cancel_changed:
        result.cancel_status_changed += 1
        _record_location(result, "cancel_changed_locations", entry.slug, entry.name)
    result.locations_created += 1
    _record_location(result, "created_locations", entry.slug, entry.name)

    if detect_duplicates:
        candidate_dates = _candidate_summary_dates(entry.slug, entry.name)
        matches = _find_duplicate_matches(db, platform, entry, candidate_dates)
        if matches:
            matched, overlap = matches[0]
            merge_request = _create_merge_request_if_needed(db, platform, entry, matched, overlap)
            if merge_request is not None:
                result.merge_requests_created += 1
                _record_location(result, "merge_request_locations", entry.slug, entry.name)
                if merge_request.notified_at is None:
                    if _notify_merge_request(merge_request):
                        merge_request.notified_at = datetime.now(timezone.utc)
                        result.merge_notifications_sent += 1
                        db.flush()


def sync_locations_registry(
    db: Session,
    options: LocationRegistrySyncOptions | None = None,
) -> LocationRegistrySyncResult:
    options = options or LocationRegistrySyncOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = LocationRegistrySyncResult()
    sync_run = _start_sync_run(db, platform)

    try:
        page, _ = bulk_parser.fetch_events_page()
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
                    fetch_missing_coordinates=options.fetch_missing_coordinates,
                    detect_duplicates=options.detect_duplicates,
                    result=result,
                )
            except Exception as exc:
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
