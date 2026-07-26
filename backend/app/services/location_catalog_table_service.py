from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.location_page_url import location_page_url
from app.models import Location, Platform
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import _location_is_cancelled, _location_is_paused
from app.services.user_unique_locations_detail import build_user_unique_location_details

MAP_PLATFORMS = ("five_verst", "s95", "runpark")


def _first_platform_visit_date(platform_payload: dict[str, object]) -> date | None:
    dates: list[date] = []
    for field in ("run_dates", "volunteer_dates"):
        for value in platform_payload.get(field, []):
            parsed = date.fromisoformat(str(value))
            if has_real_activity_date(parsed):
                dates.append(parsed)
    if not dates:
        return None
    return min(dates)


def _build_visit_index(
    details: dict[str, object],
) -> dict[str, tuple[date, str]]:
    """Первое посещение физической локации: identity → (дата, система).

    Ключ — только локация, без платформы. Раньше ключ был (локация, система), и
    посещение терялось, если система визита не совпадала с системой строки
    каталога: строки заводятся лишь для MAP_PLATFORMS, поэтому пробежка в
    parkrun-эпоху на пятивёрстовской строке давала «Не посещал». Система
    первого визита остаётся в значении — её показываем рядом с датой, чтобы
    «Посещал» на строке 5 вёрст не выглядел ошибкой, когда бегали в parkrun.
    """
    index: dict[str, tuple[date, str]] = {}
    for location in details.get("locations", []):
        identity_key = str(location["catalog_identity_key"])
        for platform in location.get("platforms", []):
            platform_code = str(platform["platform_code"])
            first_visit = _first_platform_visit_date(platform)
            if first_visit is None:
                continue
            existing = index.get(identity_key)
            if existing is None or first_visit < existing[0]:
                index[identity_key] = (first_visit, platform_code)
    return index


def build_catalog_locations_table(
    db: Session,
    user_id: UUID | None,
    *,
    include_test_events: bool = False,
) -> dict[str, object]:
    # Аноним (user_id=None) видит каталог без отметок «посещено».
    if user_id is not None:
        visit_details = build_user_unique_location_details(
            db,
            user_id,
            include_test_events=include_test_events,
        )
        visit_index = _build_visit_index(visit_details)
    else:
        visit_index = {}

    rows_query = (
        db.query(Location, Platform)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(
            Platform.code.in_(MAP_PLATFORMS),
            Location.is_official_map.is_(True),
        )
        .order_by(Platform.code.asc(), Location.name.asc())
        .all()
    )

    catalog_index = LocationCatalogIndex(db)
    rows: list[dict[str, object]] = []

    for location, platform in rows_query:
        platform_code = platform.code
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        visit = visit_index.get(identity_key)
        first_visit = visit[0] if visit is not None else None
        first_visit_platform = visit[1] if visit is not None else None
        rows.append(
            {
                "row_key": f"{location.id}:{platform_code}",
                "catalog_identity_key": identity_key,
                "location_id": str(location.id),
                "location_slug": location.external_key.strip().lower(),
                "name": catalog_index.display_name(location, platform_code),
                "city": location.city,
                "region": location.region,
                "country": location.country,
                "platform_code": platform_code,
                "is_paused": _location_is_paused(location, catalog_index, platform_code),
                "is_cancelled": _location_is_cancelled(location),
                "has_coordinates": location.latitude is not None and location.longitude is not None,
                "location_url": location_page_url(platform_code, location.external_key, location.source_url),
                "visited": first_visit is not None,
                "first_visit_date": first_visit.isoformat() if first_visit is not None else None,
                # В какой системе был самый ранний визит: у строки 5 вёрст это
                # может быть parkrun — тогда показываем это рядом с датой.
                "first_visit_platform": first_visit_platform,
            }
        )

    rows.sort(key=lambda item: (str(item["name"]).lower(), str(item["platform_code"])))
    return {
        "rows": rows,
        "total_rows": len(rows),
    }
