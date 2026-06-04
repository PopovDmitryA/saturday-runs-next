from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform, SyncStatus

logger = logging.getLogger(__name__)

DEFAULT_INACTIVE_WEEKS = 5


@dataclass
class MarkInactiveLocationsResult:
    locations_checked: int = 0
    locations_paused: int = 0
    errors: list[str] = field(default_factory=list)


def mark_inactive_locations_paused(
    db: Session,
    *,
    inactive_weeks: int = DEFAULT_INACTIVE_WEEKS,
    platform_codes: tuple[str, ...] | None = None,
    as_of: date | None = None,
) -> MarkInactiveLocationsResult:
    """Mark locations paused when their latest event is older than inactive_weeks."""
    result = MarkInactiveLocationsResult()
    cutoff = (as_of or date.today()) - timedelta(weeks=inactive_weeks)

    query = db.query(Location, Platform).join(Platform, Location.platform_id == Platform.id)
    if platform_codes:
        query = query.filter(Platform.code.in_(platform_codes))

    for location, _platform in query.all():
        result.locations_checked += 1
        if location.is_cancelled:
            continue

        latest_event_date = (
            db.query(func.max(EventSummary.event_date))
            .filter(EventSummary.location_id == location.id)
            .scalar()
        )
        if latest_event_date is None:
            continue

        if latest_event_date >= cutoff:
            continue

        if location.is_paused:
            continue

        location.is_paused = True
        location.fetched_at = datetime.now(timezone.utc)
        location.sync_status = SyncStatus.ok
        result.locations_paused += 1
        logger.info(
            "Marked location paused due to inactivity: %s (last event %s)",
            location.external_key,
            latest_event_date.isoformat(),
        )

    db.flush()
    return result
