from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.models import Event, Location, Platform, PlatformLink, RunResult, VolunteerResult
from app.services.location_catalog_service import LocationCatalogIndex


@dataclass(frozen=True)
class UniqueLocationCounts:
    unique_total: int
    unique_with_coordinates: int
    unique_without_coordinates: int


def aggregate_user_location_stats(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> dict[UUID, dict[str, object]]:
    run_query = (
        db.query(
            Location.id.label("location_id"),
            func.count(RunResult.id).label("run_count"),
            func.max(Event.event_date).label("last_visit"),
        )
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
    )
    if not include_test_events:
        run_query = run_query.filter(Event.is_test_event.is_(False))
    run_query = run_query.group_by(Location.id)

    vol_query = (
        db.query(
            Location.id.label("location_id"),
            func.count(func.distinct(Event.event_date)).label("volunteer_count"),
            func.max(Event.event_date).label("last_visit"),
        )
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        vol_query = vol_query.filter(Event.is_test_event.is_(False))
    vol_query = vol_query.group_by(Location.id)

    stats: dict[UUID, dict[str, object]] = {}

    def _ensure(location_id: UUID) -> dict[str, object]:
        if location_id not in stats:
            stats[location_id] = {
                "run_count": 0,
                "volunteer_count": 0,
                "last_visit": None,
            }
        return stats[location_id]

    for row in run_query.all():
        entry = _ensure(row.location_id)
        entry["run_count"] = int(row.run_count)
        if has_real_activity_date(row.last_visit):
            entry["last_visit"] = row.last_visit

    for row in vol_query.all():
        entry = _ensure(row.location_id)
        entry["volunteer_count"] = int(row.volunteer_count)
        if has_real_activity_date(row.last_visit):
            current = entry["last_visit"]
            if current is None or row.last_visit > current:
                entry["last_visit"] = row.last_visit

    return stats


def _normalize_geo_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def count_unique_geo_from_rows(
    rows: list[tuple[Location, str]],
) -> tuple[int, int]:
    cities: set[str] = set()
    regions: set[str] = set()
    for location, _platform_code in rows:
        city = _normalize_geo_value(location.city)
        region = _normalize_geo_value(location.region)
        if city is not None:
            cities.add(city)
        if region is not None:
            regions.add(region)
    return len(regions), len(cities)


def count_unique_locations_from_rows(
    catalog_index: LocationCatalogIndex,
    rows: list[tuple[Location, str]],
) -> UniqueLocationCounts:
    all_keys: set[str] = set()
    keys_with_coordinates: set[str] = set()

    for location, platform_code in rows:
        key = catalog_index.canonical_identity_key(location, platform_code)
        all_keys.add(key)
        latitude, longitude = catalog_index.coordinates_for(location, platform_code)
        if latitude is not None and longitude is not None:
            keys_with_coordinates.add(key)

    unique_total = len(all_keys)
    unique_with_coordinates = len(keys_with_coordinates)
    return UniqueLocationCounts(
        unique_total=unique_total,
        unique_with_coordinates=unique_with_coordinates,
        unique_without_coordinates=unique_total - unique_with_coordinates,
    )


def count_unique_locations_from_stats(
    db: Session,
    catalog_index: LocationCatalogIndex,
    stats_by_location: dict[UUID, dict[str, object]],
) -> UniqueLocationCounts:
    if not stats_by_location:
        return UniqueLocationCounts(0, 0, 0)

    rows = (
        db.query(Location, Platform)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Location.id.in_(stats_by_location.keys()))
        .all()
    )
    return count_unique_locations_from_rows(catalog_index, [(location, platform.code) for location, platform in rows])


def count_user_unique_locations(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> UniqueLocationCounts:
    stats_by_location = aggregate_user_location_stats(
        db, user_id, include_test_events=include_test_events
    )
    catalog_index = LocationCatalogIndex(db)
    return count_unique_locations_from_stats(db, catalog_index, stats_by_location)
