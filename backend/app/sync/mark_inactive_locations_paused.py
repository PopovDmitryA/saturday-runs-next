from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform, SyncStatus
from app.services.location_activity_status import INACTIVE_AFTER_DAYS
from app.services.location_catalog_cache import flush_location_catalog_caches

logger = logging.getLogger(__name__)

# Оставлено ради старых вызовов снеделями: скрипт принимает недели, а порог живёт
# в днях рядом с остальной логикой статуса.
DEFAULT_INACTIVE_WEEKS = INACTIVE_AFTER_DAYS // 7


@dataclass
class MarkInactiveLocationsResult:
    locations_checked: int = 0
    locations_paused: int = 0
    locations_revived: int = 0
    errors: list[str] = field(default_factory=list)


def mark_inactive_locations_paused(
    db: Session,
    *,
    inactive_days: int = INACTIVE_AFTER_DAYS,
    platform_codes: tuple[str, ...] | None = None,
    as_of: date | None = None,
) -> MarkInactiveLocationsResult:
    """Проставить «не действует» площадкам, молчащим дольше порога.

    Работает в обе стороны: площадка, вернувшаяся после долгого перерыва,
    снимает с себя статус сама (Бузулук зимовал 126 дней и вернулся). Не
    трогаем те, что помечены реестром системы вручную, — там заявление
    владельца, и оно сильнее нашей догадки по датам.
    """
    result = MarkInactiveLocationsResult()
    cutoff = (as_of or date.today()) - timedelta(days=inactive_days)

    query = db.query(Location, Platform).join(Platform, Location.platform_id == Platform.id)
    if platform_codes:
        query = query.filter(Platform.code.in_(platform_codes))

    for location, _platform in query.all():
        result.locations_checked += 1
        # Отмена ближайшего старта статуса не меняет: площадка работает, просто
        # в эту субботу не побегут.
        if location.is_upcoming:
            continue

        latest_event_date = (
            db.query(func.max(EventSummary.event_date))
            .filter(EventSummary.location_id == location.id)
            .scalar()
        )
        if latest_event_date is None:
            continue

        should_pause = latest_event_date < cutoff
        if should_pause == location.is_paused:
            continue

        location.is_paused = should_pause
        location.fetched_at = datetime.now(timezone.utc)
        location.sync_status = SyncStatus.ok
        if should_pause:
            result.locations_paused += 1
            logger.info(
                "Локация ушла в «не действует»: %s (последний старт %s)",
                location.external_key,
                latest_event_date.isoformat(),
            )
        else:
            result.locations_revived += 1
            logger.info(
                "Локация вернулась в строй: %s (последний старт %s)",
                location.external_key,
                latest_event_date.isoformat(),
            )

    db.flush()
    if result.locations_paused or result.locations_revived:
        flush_location_catalog_caches("правило молчания")
    return result
