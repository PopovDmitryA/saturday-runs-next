from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.location_page_url import location_page_url, pick_primary_location_url
from app.models import Location, Platform
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.user_unique_locations_detail import build_user_unique_location_details


def _collect_dates(platforms: list[dict[str, object]], field: str) -> list[str]:
    values: set[str] = set()
    for platform in platforms:
        for value in platform.get(field, []):
            values.add(str(value))
    return sorted(values, reverse=True)


def _location_is_paused(location: Location, catalog_index: LocationCatalogIndex, platform_code: str) -> bool:
    if location.is_cancelled:
        return False
    if location.is_paused:
        return True
    catalog = catalog_index.get_for_location(location, platform_code)
    return catalog.is_closed if catalog is not None else False


def _location_is_cancelled(location: Location) -> bool:
    return bool(location.is_cancelled)


def list_user_visited_map_locations(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> dict[str, object]:
    payload = build_user_unique_location_details(
        db, user_id, include_test_events=include_test_events
    )
    points: list[dict[str, object]] = []
    for location in payload["locations"]:
        if not location.get("has_coordinates"):
            continue
        platforms = list(location.get("platforms", []))
        platform_codes = [str(item["platform_code"]) for item in platforms]
        urls_by_platform = {
            str(item["platform_code"]): item.get("location_url")
            for item in platforms
            if item.get("location_url")
        }
        points.append(
            {
                "id": str(location["catalog_identity_key"]),
                "catalog_identity_key": location["catalog_identity_key"],
                "location_slug": location.get("location_slug"),
                "name": location["name"],
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "city": location["city"],
                "region": location.get("region"),
                "platform_codes": platform_codes,
                "active_platform": None,
                "location_url": pick_primary_location_url(
                    platform_codes,
                    urls_by_platform=urls_by_platform,
                ),
                "is_paused": bool(location.get("is_paused")),
                "is_cancelled": bool(location.get("is_cancelled")),
                "run_count": location["run_count"],
                "volunteer_count": location["volunteer_count"],
                "visit_count": int(location["run_count"]) + int(location["volunteer_count"]),
                "last_visit_date": location["last_visit_date"],
                "run_dates": _collect_dates(platforms, "run_dates"),
                "volunteer_dates": _collect_dates(platforms, "volunteer_dates"),
                "platform_visits": platforms,
            }
        )

    points.sort(key=lambda item: (-int(item["visit_count"]), str(item["name"])))
    return {
        "points": points,
        "total_locations": payload["total_locations"],
        "mapped_locations": payload["mapped_locations"],
        "unmapped_locations": payload["unmapped_locations"],
    }


def list_catalog_map_locations(db: Session) -> dict[str, object]:
    rows = (
        db.query(Location, Platform)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            Platform.code.in_(("five_verst", "s95", "runpark")),
            Location.is_official_map.is_(True),
        )
        .order_by(Platform.code.asc(), Location.name.asc())
        .all()
    )

    catalog_index = LocationCatalogIndex(db)
    buckets: dict[str, dict[str, object]] = {}
    official_total = 0
    unmapped = 0

    for location, platform in rows:
        official_total += 1
        if location.latitude is None or location.longitude is None:
            unmapped += 1
            continue

        identity_key = catalog_index.canonical_identity_key(location, platform.code)
        bucket = buckets.get(identity_key)
        if bucket is None:
            bucket = {
                "id": str(location.id),
                "catalog_identity_key": identity_key,
                "location_slug": location.external_key.strip().lower(),
                "name": catalog_index.display_name(location, platform.code),
                "latitude": location.latitude,
                "longitude": location.longitude,
                "city": location.city,
                "region": location.region,
                "platform_codes": set(),
                "platform_urls": {},
                "active_platform": platform.code,
                "is_paused": _location_is_paused(location, catalog_index, platform.code),
                "is_cancelled": _location_is_cancelled(location),
                "run_count": 0,
                "volunteer_count": 0,
                "visit_count": 0,
                "last_visit_date": None,
                "run_dates": [],
                "volunteer_dates": [],
                "platform_visits": [],
            }
            buckets[identity_key] = bucket
        else:
            if len(catalog_index.display_name(location, platform.code)) > len(str(bucket["name"])):
                bucket["name"] = catalog_index.display_name(location, platform.code)
            if location.region and not bucket.get("region"):
                bucket["region"] = location.region
            bucket["is_paused"] = bool(bucket["is_paused"]) or _location_is_paused(
                location,
                catalog_index,
                platform.code,
            )
            bucket["is_cancelled"] = bool(bucket.get("is_cancelled")) or _location_is_cancelled(location)

        platform_codes: set[str] = bucket["platform_codes"]  # type: ignore[assignment]
        platform_codes.add(platform.code)
        page_url = location_page_url(platform.code, location.external_key, location.source_url)
        if page_url:
            urls: dict[str, str] = bucket["platform_urls"]  # type: ignore[assignment]
            urls[platform.code] = page_url

    points: list[dict[str, object]] = []
    for bucket in buckets.values():
        platform_codes = sorted(bucket["platform_codes"])  # type: ignore[arg-type]
        active = platform_codes[0] if len(platform_codes) == 1 else None
        platform_urls: dict[str, str] = bucket.get("platform_urls") or {}  # type: ignore[assignment]
        point = {
            key: value for key, value in bucket.items() if key not in {"platform_codes", "platform_urls"}
        }
        points.append(
            {
                **point,
                "platform_codes": platform_codes,
                "active_platform": active,
                "location_url": pick_primary_location_url(
                    platform_codes,
                    active_platform=active,
                    urls_by_platform=platform_urls,
                ),
            }
        )

    points.sort(key=lambda item: str(item["name"]).lower())

    return {
        "points": points,
        "total_locations": official_total,
        "mapped_locations": len(points),
        "unmapped_locations": unmapped,
    }
