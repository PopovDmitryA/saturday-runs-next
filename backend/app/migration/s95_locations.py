from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.migration.helpers import (
    parse_float,
    s95_country_from_url,
    slug_from_s95_url,
)
from app.migration.legacy_db import legacy_rows
from app.migration.stats import MigrationStats
from app.platform_adapters.canonical import CanonicalLocation
from app.sync import upsert

PLATFORM_CODE = "s95"


def migrate_locations(
    db: Session,
    conn,
    *,
    dry_run: bool,
    stats: MigrationStats | None = None,
) -> MigrationStats:
    stats = stats or MigrationStats()
    platform = upsert.get_platform(db, PLATFORM_CODE)

    for row in legacy_rows(
        conn,
        """
        SELECT
            link_point,
            name_point,
            full_name_point,
            city,
            region,
            latitude,
            longitude,
            is_pause
        FROM s95_location
        WHERE link_point IS NOT NULL
        ORDER BY name_point
        """,
    ):
        external_key = slug_from_s95_url(str(row["link_point"]))
        if not external_key:
            stats.skipped += 1
            stats.add_error(f"s95 location: cannot parse slug from {row['link_point']!r}")
            continue

        name = str(row.get("full_name_point") or row.get("name_point") or external_key).strip()
        source_url = str(row["link_point"]).strip()
        location = CanonicalLocation(
            external_key=external_key,
            name=name,
            country=s95_country_from_url(source_url),
            city=(str(row["city"]).strip() if row.get("city") else None),
            region=(str(row["region"]).strip() if row.get("region") else None),
            latitude=parse_float(row.get("latitude")),
            longitude=parse_float(row.get("longitude")),
            source_url=source_url,
        )
        if dry_run:
            stats.locations += 1
            continue

        location_row, _ = upsert.upsert_location(db, platform, location)
        if row.get("is_pause"):
            location_row.is_paused = bool(row["is_pause"])
        stats.locations += 1

    if not dry_run:
        db.commit()
    return stats
