from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.models import Location, Platform
from app.sync import upsert
from app.sync.s95_global_sync import (
    S95LocationSyncOptions,
    S95LocationSyncResult,
    configured_s95_locations,
    sync_s95_location,
)

logger = logging.getLogger(__name__)

ROTATION_INDEX_KEY = "s95:location_rotation:index"
PLATFORM_CODE = "s95"


@dataclass
class S95LocationRotationSyncResult:
    location_external_key: str
    rotation_index: int
    locations_total: int
    sync: S95LocationSyncResult | None = None
    errors: list[str] = field(default_factory=list)


def _rotation_locations(db: Session, platform: Platform) -> list[tuple[str, str, str]]:
    rows = (
        db.query(Location)
        .filter(Location.platform_id == platform.id)
        .order_by(Location.external_key.asc())
        .all()
    )
    if rows:
        return [
            (
                row.external_key,
                row.name,
                row.source_url or f"https://s95.ru/events/{row.external_key}",
            )
            for row in rows
        ]
    return [
        (loc.location_external_key, loc.location_name, loc.location_source_url)
        for loc in configured_s95_locations()
    ]


def _next_rotation_location(
    locations: list[tuple[str, str, str]],
) -> tuple[tuple[str, str, str], int, int]:
    if not locations:
        raise RuntimeError("No S95 locations in registry")
    redis = get_redis_client()
    raw = redis.get(ROTATION_INDEX_KEY)
    index = int(raw) if raw is not None else 0
    item = locations[index % len(locations)]
    next_index = (index + 1) % len(locations)
    redis.set(ROTATION_INDEX_KEY, str(next_index))
    return item, index, len(locations)


def sync_next_s95_location_batch(db: Session) -> S95LocationRotationSyncResult:
    settings = get_settings()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    locations = _rotation_locations(db, platform)
    (external_key, name, source_url), index, total = _next_rotation_location(locations)
    logger.info("S95 location rotation: slug=%s index=%s/%s", external_key, index, total)

    try:
        sync_result = sync_s95_location(
            db,
            S95LocationSyncOptions(
                location_external_key=external_key,
                location_name=name,
                location_source_url=source_url,
                summaries_limit=settings.s95_location_batch_summaries_limit,
                protocol_fetch_limit=settings.s95_sync_protocol_limit,
                fetch_all_protocols_on_change=settings.s95_fetch_all_protocols_on_change,
            ),
        )
        return S95LocationRotationSyncResult(
            location_external_key=external_key,
            rotation_index=index,
            locations_total=total,
            sync=sync_result,
            errors=list(sync_result.errors),
        )
    except Exception as exc:
        logger.exception("S95 location rotation failed for %s", external_key)
        return S95LocationRotationSyncResult(
            location_external_key=external_key,
            rotation_index=index,
            locations_total=total,
            errors=[str(exc)],
        )
