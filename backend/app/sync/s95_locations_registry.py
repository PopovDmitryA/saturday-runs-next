from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.migration.helpers import s95_country_from_url
from app.models import Location, Platform, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalLocation
from app.s95.api_client import S95ApiLocation, fetch_all_locations
from app.s95.fetch.priority import S95YieldForUserSync
from app.services.location_cancellation_notify import (
    CancellationChange,
    notify_cancellation_changes,
)
from app.services.location_catalog_cache import (
    flush_location_catalog_caches,
    flush_location_page_caches,
)
from app.services.location_geo_service import apply_reverse_geocode_to_location
from app.services.sync_report_labels import location_detail_label
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step
from app.sync.location_registry_status import apply_location_registry_flags
from app.sync.s95_location_status import ACTIVE_STATUS, S95LocationStatus, resolve_s95_location_status

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
    pause_status_changed: int = 0
    cancel_status_changed: int = 0
    cancel_changed_locations: list[str] = field(default_factory=list)
    # Изменения отмены в разобранном виде — для мониторинга; в отчёт синка не
    # идут (там уже есть cancel_changed_locations).
    cancellation_changes: list[CancellationChange] = field(default_factory=list, repr=False)
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
    source_url = f"{entry.domain}/events/{entry.slug}"
    return CanonicalLocation(
        external_key=entry.slug,
        name=entry.name,
        country=s95_country_from_url(source_url),
        city=entry.town or None,
        latitude=entry.latitude,
        longitude=entry.longitude,
        source_url=source_url,
    )


def _entry_status(entry: S95ApiLocation, result: S95LocationRegistrySyncResult) -> S95LocationStatus | None:
    """Что реестр говорит про площадку — с походом на страницу для неработающих.

    `entry.active=false` само по себе неоднозначно: так выглядит и закрытая
    навсегда площадка, и отменённая суббота (Иваново 27.08.2026). Разбирает их
    `resolve_s95_location_status` по красной плашке страницы. Если страница не
    открылась, возвращаем None — признаки строки лучше оставить прежними, чем
    записать в них догадку.
    """
    if entry.active:
        return ACTIVE_STATUS
    try:
        return resolve_s95_location_status(entry)
    except Exception as exc:
        result.errors.append(f"{entry.slug}: статус — {exc}")
        logger.warning("S95 status resolve failed for %s: %s", entry.slug, exc, exc_info=True)
        return None


def _apply_status(
    row: Location,
    status: S95LocationStatus | None,
    entry: S95ApiLocation,
    result: S95LocationRegistrySyncResult,
) -> bool:
    if status is None:
        return False
    changed, pause_changed, cancel_changed = apply_location_registry_flags(
        row,
        is_paused=status.is_paused,
        is_cancelled=status.is_cancelled,
        cancel_reason=status.cancel_reason,
    )
    if pause_changed:
        result.pause_status_changed += 1
    if cancel_changed:
        result.cancel_status_changed += 1
        result.cancel_changed_locations.append(location_detail_label(entry.slug, entry.name))
        result.cancellation_changes.append(
            CancellationChange(
                platform_code=PLATFORM_CODE,
                slug=entry.slug,
                name=entry.name,
                cancelled=status.is_cancelled,
                reason=status.cancel_reason,
            )
        )
    return changed


def _process_entry(
    db: Session,
    platform: Platform,
    entry: S95ApiLocation,
    result: S95LocationRegistrySyncResult,
) -> None:
    status = _entry_status(entry, result)

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
        _apply_status(row, status, entry, result)
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

    # Страна по домену реестра — единственный надёжный признак: s95 ведёт
    # Сербию на s95.rs, Беларусь на s95.by, Россию на s95.ru. Раньше страну
    # никто не проставлял, а upsert_location пустым значением не затирает
    # известное — так Белград и Гродно навсегда оставались «Россией».
    # Тут перезаписываем, а не дозаполняем: домен точнее любого прошлого значения.
    country = s95_country_from_url(source_url)
    if row.country != country:
        row.country = country
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

    # Координаты есть, а флага нет — так остаётся локация, которую первым увидел
    # не реестр, а протокол (_ensure_location в s95_global_sync_api). Ветка выше
    # трогает флаг только когда координаты ДОЗАПОЛНЯЮТСЯ, поэтому такая строка
    # навсегда оставалась вне карты и каталога — так пропало Кратово. У 5 вёрст
    # это лечение есть (five_verst_locations), приводим s95 к тому же поведению.
    if row.latitude is not None and row.longitude is not None and not row.is_official_map:
        row.is_official_map = True
        changed = True

    if _apply_status(row, status, entry, result):
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
        try:
            entries = fetch_all_locations()
        except S95YieldForUserSync:
            # Пользовательский синк вперёд: реестр подождёт следующего прохода.
            logger.info("S95 registry sync: уступили пользовательскому синку")
            _finish_sync_run(db, sync_run, success=True, fetched=0, upserted=0, unchanged=0, error=None)
            db.commit()
            return result
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
        if result.locations_created or result.locations_updated:
            flush_location_catalog_caches("синк реестра s95")
        if result.cancellation_changes:
            flush_location_page_caches(
                db,
                [item.slug for item in result.cancellation_changes],
                "синк реестра s95",
            )
            notify_cancellation_changes(result.cancellation_changes)
        return result

    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform)
        _finish_sync_run(db, failed_run, success=False, fetched=0, upserted=0, unchanged=0, error=str(exc))
        db.commit()
        raise
