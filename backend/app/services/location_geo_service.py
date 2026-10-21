from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.geo.reverse_geocode import lookup_address
from app.models import Location, Platform
from app.sync.iteration_commit import release_before_fetch

logger = logging.getLogger(__name__)

MAP_PLATFORMS = ("five_verst", "s95", "runpark")


@dataclass
class LocationGeoBackfillResult:
    candidates_total: int = 0
    regions_backfilled: int = 0
    coords_fetched: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)


def apply_reverse_geocode_to_location(location: Location) -> bool:
    if location.latitude is None or location.longitude is None:
        return False
    if location.region is not None and location.city is not None and location.country is not None:
        return False

    try:
        address = lookup_address(location.latitude, location.longitude)
    except Exception:
        logger.warning("Reverse geocode failed for %s", location.external_key, exc_info=True)
        return False
    return _apply_address(location, address)


def _apply_address(location: Location, address: dict[str, str | None]) -> bool:
    """Дописать пустые страну, регион и город из ответа геокодера."""
    changed = False
    if location.region is None and address.get("region"):
        location.region = address["region"]
        changed = True
    if location.city is None and address.get("city"):
        location.city = address["city"]
        changed = True
    if location.country is None and address.get("country"):
        location.country = address["country"]
        changed = True

    if changed:
        location.fetched_at = datetime.now(timezone.utc)
    return changed


def _geo_backfill_candidates_query(
    db: Session,
    *,
    platform_codes: tuple[str, ...],
    official_map_only: bool,
):
    query = (
        db.query(Location, Platform)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            Platform.code.in_(platform_codes),
            Location.latitude.isnot(None),
            Location.longitude.isnot(None),
        )
    )
    if official_map_only:
        query = query.filter(Location.is_official_map.is_(True))
    return query.filter(
        (Location.region.is_(None)) | (Location.city.is_(None)) | (Location.country.is_(None))
    ).order_by(Location.fetched_at.asc().nullsfirst(), Location.name.asc())


def _coords_retry_candidates_query(
    db: Session,
    *,
    platform_code: str,
    official_map_only: bool,
):
    query = (
        db.query(Location, Platform)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            Platform.code == platform_code,
            Location.latitude.is_(None) | Location.longitude.is_(None),
        )
    )
    if official_map_only:
        query = query.filter(Location.is_official_map.is_(True))
    return query.order_by(Location.name.asc())


def backfill_location_geo(
    db: Session,
    *,
    limit: int = 50,
    platform_codes: tuple[str, ...] = MAP_PLATFORMS,
    official_map_only: bool = True,
    fetch_missing_coordinates: bool = False,
) -> LocationGeoBackfillResult:
    result = LocationGeoBackfillResult()

    if fetch_missing_coordinates:
        if "five_verst" not in platform_codes:
            result.errors.append("fetch_missing_coordinates supports five_verst only")
            return result

        from app.platform_adapters.canonical import CanonicalLocation
        from app.platform_adapters.five_verst import bulk_parser
        from app.sync import upsert
        from app.sync.five_verst_locations import _enrich_location_geo

        # Поля кандидатов — в локальные переменные до похода в сеть: каждая
        # локация — фетч 5 вёрст (лок и rate-limit, десятки секунд) плюс
        # Nominatim, а прод рвёт «idle in transaction» через 60 с. Поэтому сеть
        # идёт без открытой транзакции, а каждая локация пишется своим коммитом.
        candidates = [
            (location.external_key, location.name, location.country, location.city, location.region, location.source_url)
            for location, _platform in _coords_retry_candidates_query(
                db,
                platform_code="five_verst",
                official_map_only=official_map_only,
            ).limit(limit).all()
        ]
        result.candidates_total = len(candidates)

        platform = upsert.get_platform(db, "five_verst")
        for slug, name, country, city, region, source_url in candidates:
            release_before_fetch(db)
            try:
                location_data, location_html = bulk_parser.fetch_location(slug)
                if location_data.latitude is None or location_data.longitude is None:
                    result.unchanged += 1
                    continue
                location_data = _enrich_location_geo(location_data)
                location_data = CanonicalLocation(
                    external_key=location_data.external_key,
                    name=name or location_data.name,
                    country=location_data.country or country,
                    city=location_data.city or city,
                    region=location_data.region or region,
                    latitude=location_data.latitude,
                    longitude=location_data.longitude,
                    source_url=source_url or location_data.source_url,
                    course_source_url=location_data.course_source_url,
                    map_url=location_data.map_url,
                )
                updated_row, changed = upsert.upsert_location(
                    db,
                    platform,
                    location_data,
                    source_hash=bulk_parser.source_hash(location_html),
                )
                fetched = changed or updated_row.latitude is not None
                db.commit()
                if fetched:
                    result.coords_fetched += 1
                else:
                    result.unchanged += 1
            except Exception as exc:
                db.rollback()
                result.errors.append(f"{slug}: {exc}")
        db.commit()
        return result

    # Nominatim — секунда на запрос, кандидатов десятки: раньше всё шло в
    # одной транзакции от SELECT до общего коммита, и прод рвал её через 60 с
    # простоя. Теперь геокодим без транзакции, по id и координатам, а каждую
    # локацию пишем коротким коммитом.
    candidates = [
        (location.id, location.external_key, location.latitude, location.longitude)
        for location, _platform in _geo_backfill_candidates_query(
            db,
            platform_codes=platform_codes,
            official_map_only=official_map_only,
        ).limit(limit).all()
    ]
    result.candidates_total = len(candidates)

    for location_id, external_key, latitude, longitude in candidates:
        release_before_fetch(db)
        try:
            address = lookup_address(latitude, longitude)
        except Exception:
            logger.warning("Reverse geocode failed for %s", external_key, exc_info=True)
            result.unchanged += 1
            continue
        location = db.get(Location, location_id)
        if location is not None and _apply_address(location, address):
            db.commit()
            result.regions_backfilled += 1
        else:
            result.unchanged += 1

    db.commit()
    return result
