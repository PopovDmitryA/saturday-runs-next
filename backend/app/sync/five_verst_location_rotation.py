from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.platform_adapters.five_verst import bulk_parser
from app.sync.global_sync import LocationSyncOptions, LocationSyncResult, sync_location

logger = logging.getLogger(__name__)

ROTATION_INDEX_KEY = "five_verst:location_rotation:index"


@dataclass
class LocationRotationSyncResult:
    location_slug: str
    rotation_index: int
    locations_total: int
    sync: LocationSyncResult | None = None
    errors: list[str] = field(default_factory=list)


def _next_rotation_slug(slugs: list[str]) -> tuple[str, int, int]:
    if not slugs:
        raise RuntimeError("No 5verst locations in registry")
    redis = get_redis_client()
    raw = redis.get(ROTATION_INDEX_KEY)
    index = int(raw) if raw is not None else 0
    slug = slugs[index % len(slugs)]
    next_index = (index + 1) % len(slugs)
    redis.set(ROTATION_INDEX_KEY, str(next_index))
    return slug, index, len(slugs)


def sync_next_location_batch(db: Session) -> LocationRotationSyncResult:
    """
    Rotate through official locations: each run checks up to N summaries for one slug.
    Protocols are fetched only for changed or missing summaries (see sync_location).
    """
    settings = get_settings()
    slugs = bulk_parser.list_location_slugs()
    slug, index, total = _next_rotation_slug(slugs)
    logger.info("5verst location rotation: slug=%s index=%s/%s", slug, index, total)

    try:
        sync_result = sync_location(
            db,
            LocationSyncOptions(
                location_slug=slug,
                summaries_limit=settings.five_verst_location_batch_summaries_limit,
                protocol_fetch_limit=None,
                fetch_all_protocols_on_change=True,
                location_refresh_interval_days=settings.five_verst_location_refresh_interval_days,
            ),
        )
        return LocationRotationSyncResult(
            location_slug=slug,
            rotation_index=index,
            locations_total=total,
            sync=sync_result,
            errors=list(sync_result.errors),
        )
    except Exception as exc:
        return LocationRotationSyncResult(
            location_slug=slug,
            rotation_index=index,
            locations_total=total,
            errors=[str(exc)],
        )
