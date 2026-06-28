from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.location_page_url import location_page_url
from app.models import Event, Location, Platform, PlatformLink, RunResult, VolunteerResult
from app.services.location_catalog_service import LocationCatalogIndex, should_use_catalog_display
from app.services.user_location_stats import count_unique_locations_from_rows

PLATFORM_ORDER = ("five_verst", "s95", "parkrun", "runpark")


def _platform_sort_key(code: str) -> tuple[int, str]:
    try:
        return (PLATFORM_ORDER.index(code), code)
    except ValueError:
        return (len(PLATFORM_ORDER), code)


def _sorted_roles(values: Counter[str]) -> list[dict[str, object]]:
    return [
        {"role": role, "count": count}
        for role, count in sorted(values.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def _sorted_dates(values: set[date]) -> list[str]:
    return [value.isoformat() for value in sorted(values, reverse=True)]


def _normalize_role(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _pick_coordinates(
    current: tuple[float | None, float | None],
    location: Location,
    platform_code: str,
    catalog_index: LocationCatalogIndex,
) -> tuple[float | None, float | None]:
    latitude, longitude = current
    if latitude is not None and longitude is not None:
        return current
    return catalog_index.coordinates_for(location, platform_code)


def _finalize_bucket(
    bucket: dict[str, object],
    catalog_index: LocationCatalogIndex,
) -> None:
    identity_key = str(bucket["catalog_identity_key"])
    catalog = catalog_index.get_for_identity_key(identity_key)
    if catalog is not None and catalog.canonical_name:
        bucket["name"] = catalog.canonical_name
    if bucket["latitude"] is None or bucket["longitude"] is None:
        latitude, longitude = catalog_index.coordinates_for_identity_key(identity_key)
        if latitude is not None and longitude is not None:
            bucket["latitude"] = latitude
            bucket["longitude"] = longitude
    bucket["is_paused"] = catalog_index.is_paused_identity_key(identity_key)


def build_user_unique_location_details(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> dict[str, object]:
    runs_query = (
        db.query(Event.event_date, Location, Platform.code)
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
    )
    vol_query = (
        db.query(Event.event_date, Location, Platform.code, VolunteerResult.role)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        runs_query = runs_query.filter(Event.is_test_event.is_(False))
        vol_query = vol_query.filter(Event.is_test_event.is_(False))

    run_rows = runs_query.all()
    vol_rows = vol_query.all()

    catalog_index = LocationCatalogIndex(db)
    buckets: dict[str, dict[str, object]] = {}

    def _ensure_bucket(identity_key: str) -> dict[str, object]:
        bucket = buckets.get(identity_key)
        if bucket is None:
            bucket = {
                "catalog_identity_key": identity_key,
                "name": "",
                "city": None,
                "region": None,
                "latitude": None,
                "longitude": None,
                "run_count": 0,
                "run_dates": set(),
                "volunteer_count": 0,
                "first_visit_date": None,
                "last_visit_date": None,
                "is_paused": False,
                "is_cancelled": False,
                "platforms": {},
            }
            buckets[identity_key] = bucket
        return bucket

    def _ensure_platform(bucket: dict[str, object], platform_code: str, location: Location) -> dict[str, object]:
        platforms: dict[str, dict[str, object]] = bucket["platforms"]  # type: ignore[assignment]
        location_name = catalog_index.display_name(location, platform_code)
        platform_bucket = platforms.get(platform_code)
        page_url = location_page_url(platform_code, location.external_key, location.source_url)
        if platform_bucket is None:
            platform_bucket = {
                "platform_code": platform_code,
                "location_name": location_name,
                "location_url": page_url,
                "run_dates": set(),
                "volunteer_dates": set(),
                "volunteer_roles": Counter(),
            }
            platforms[platform_code] = platform_bucket
        else:
            catalog = catalog_index.get_for_location(location, platform_code)
            if catalog is not None and should_use_catalog_display(catalog, platform_code):
                platform_bucket["location_name"] = catalog.canonical_name
            elif len(location_name) > len(str(platform_bucket["location_name"])):
                platform_bucket["location_name"] = location_name
            if page_url and not platform_bucket.get("location_url"):
                platform_bucket["location_url"] = page_url
        return platform_bucket

    def _register_visit(
        *,
        event_date: date,
        location: Location,
        platform_code: str,
        kind: str,
        role: str | None = None,
    ) -> None:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        bucket = _ensure_bucket(identity_key)
        display_name = catalog_index.display_name(location, platform_code)
        catalog = catalog_index.get_for_location(location, platform_code)
        if catalog is not None and should_use_catalog_display(catalog, platform_code):
            bucket["name"] = catalog.canonical_name
        elif len(display_name) >= len(str(bucket["name"])):
            bucket["name"] = display_name
        if location.city and not bucket["city"]:
            bucket["city"] = location.city
        if location.region and not bucket["region"]:
            bucket["region"] = location.region
        bucket["latitude"], bucket["longitude"] = _pick_coordinates(
            (bucket["latitude"], bucket["longitude"]),
            location,
            platform_code,
            catalog_index,
        )
        if catalog_index.is_paused(location, platform_code):
            bucket["is_paused"] = True
        if getattr(location, "is_cancelled", False):
            bucket["is_cancelled"] = True

        platform_bucket = _ensure_platform(bucket, platform_code, location)
        if kind == "run":
            platform_bucket["run_dates"].add(event_date)  # type: ignore[union-attr]
            all_run_dates: set[date] = bucket["run_dates"]  # type: ignore[assignment]
            if event_date not in all_run_dates:
                all_run_dates.add(event_date)
                bucket["run_count"] = int(bucket["run_count"]) + 1
        else:
            dates_set = platform_bucket["volunteer_dates"]  # type: ignore[assignment]
            is_new_date = event_date not in dates_set
            dates_set.add(event_date)
            if is_new_date:
                bucket["volunteer_count"] = int(bucket["volunteer_count"]) + 1
            normalized_role = _normalize_role(role)
            if normalized_role is not None:
                roles: Counter[str] = platform_bucket["volunteer_roles"]  # type: ignore[assignment]
                roles[normalized_role] += 1

        if has_real_activity_date(event_date):
            first_visit = bucket["first_visit_date"]
            last_visit = bucket["last_visit_date"]
            if first_visit is None or event_date < first_visit:
                bucket["first_visit_date"] = event_date
            if last_visit is None or event_date > last_visit:
                bucket["last_visit_date"] = event_date

    for event_date, location, platform_code in run_rows:
        _register_visit(event_date=event_date, location=location, platform_code=platform_code, kind="run")

    for event_date, location, platform_code, role in vol_rows:
        _register_visit(
            event_date=event_date,
            location=location,
            platform_code=platform_code,
            kind="volunteer",
            role=role,
        )

    run_unique_counts = count_unique_locations_from_rows(
        catalog_index,
        [(location, platform_code) for _, location, platform_code in run_rows],
    )
    vol_unique_counts = count_unique_locations_from_rows(
        catalog_index,
        [(location, platform_code) for _, location, platform_code, _ in vol_rows],
    )
    all_unique_counts = count_unique_locations_from_rows(
        catalog_index,
        [(location, platform_code) for _, location, platform_code in run_rows]
        + [(location, platform_code) for _, location, platform_code, _ in vol_rows],
    )

    locations: list[dict[str, object]] = []
    for bucket in buckets.values():
        _finalize_bucket(bucket, catalog_index)
        platforms_map: dict[str, dict[str, object]] = bucket["platforms"]  # type: ignore[assignment]
        platforms = []
        for platform_code in sorted(platforms_map.keys(), key=_platform_sort_key):
            platform_bucket = platforms_map[platform_code]
            platforms.append(
                {
                    "platform_code": platform_code,
                    "location_name": platform_bucket["location_name"],
                    "location_url": platform_bucket.get("location_url"),
                    "run_dates": _sorted_dates(platform_bucket["run_dates"]),  # type: ignore[arg-type]
                    "volunteer_dates": _sorted_dates(platform_bucket["volunteer_dates"]),  # type: ignore[arg-type]
                    "volunteer_roles": _sorted_roles(platform_bucket["volunteer_roles"]),  # type: ignore[arg-type]
                }
            )
        first_visit = bucket["first_visit_date"]
        last_visit = bucket["last_visit_date"]
        locations.append(
            {
                "catalog_identity_key": bucket["catalog_identity_key"],
                "name": bucket["name"],
                "city": bucket["city"],
                "region": bucket["region"],
                "latitude": bucket["latitude"],
                "longitude": bucket["longitude"],
                "has_coordinates": bucket["latitude"] is not None and bucket["longitude"] is not None,
                "is_paused": bool(bucket["is_paused"]),
                "is_cancelled": bool(bucket.get("is_cancelled")),
                "run_count": bucket["run_count"],
                "volunteer_count": bucket["volunteer_count"],
                "first_visit_date": first_visit.isoformat() if has_real_activity_date(first_visit) else None,
                "last_visit_date": last_visit.isoformat() if has_real_activity_date(last_visit) else None,
                "platforms": platforms,
            }
        )

    locations.sort(key=lambda item: str(item["name"]).lower())

    platform_summary: dict[str, int] = defaultdict(int)
    for location in locations:
        for platform in location["platforms"]:  # type: ignore[union-attr]
            platform_summary[str(platform["platform_code"])] += 1

    return {
        "locations": locations,
        "total_locations": all_unique_counts.unique_total,
        "unique_run_locations": run_unique_counts.unique_total,
        "unique_volunteer_locations": vol_unique_counts.unique_total,
        "mapped_locations": all_unique_counts.unique_with_coordinates,
        "unmapped_locations": all_unique_counts.unique_without_coordinates,
        "platform_summary": [
            {"platform_code": code, "location_count": platform_summary[code]}
            for code in sorted(platform_summary.keys(), key=_platform_sort_key)
        ],
    }
