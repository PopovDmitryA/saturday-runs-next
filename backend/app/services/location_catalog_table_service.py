from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.location_page_url import location_page_url
from app.models import Location, Platform
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import (
    MAP_HISTORIC_PLATFORM,
    _location_is_cancelled,
    _location_is_paused,
    inherited_geo_fields,
    is_russian_historic,
    map_location_filter,
    map_platform_order,
)
from app.services.user_unique_locations_detail import build_user_unique_location_details


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
) -> dict[str, dict[str, date]]:
    """Первые посещения физической локации: identity → {система: дата}.

    Ключ — только локация, без платформы. Раньше ключ был (локация, система), и
    посещение терялось, если система визита не совпадала с системой строки
    каталога: строки заводятся лишь для действующих систем, поэтому пробежка в
    parkrun-эпоху на пятивёрстовской строке давала «Не посещал».

    Разбивку по системам не схлопываем: фильтр систем на карте живёт на фронте,
    и при сужении до 5 вёрст локация, где бегали только в parkrun, обязана снова
    стать непосещённой. Свести к одной дате здесь — значит потерять эту
    возможность.
    """
    index: dict[str, dict[str, date]] = {}
    for location in details.get("locations", []):
        identity_key = str(location["catalog_identity_key"])
        for platform in location.get("platforms", []):
            platform_code = str(platform["platform_code"])
            first_visit = _first_platform_visit_date(platform)
            if first_visit is None:
                continue
            by_platform = index.setdefault(identity_key, {})
            existing = by_platform.get(platform_code)
            if existing is None or first_visit < existing:
                by_platform[platform_code] = first_visit
    return index


def _earliest_visit(by_platform: dict[str, date] | None) -> tuple[date, str] | None:
    if not by_platform:
        return None
    platform_code, visit_date = min(by_platform.items(), key=lambda item: (item[1], item[0]))
    return visit_date, platform_code


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
        .filter(map_location_filter())
        .order_by(map_platform_order(), Platform.code.asc(), Location.name.asc())
        .all()
    )

    catalog_index = LocationCatalogIndex(db)
    rows: list[dict[str, object]] = []
    # См. map_platform_order: действующие строки идут первыми, поэтому к моменту
    # разбора parkrun-строки её связка уже здесь.
    live_locations: dict[str, Location] = {}
    historic_seen: set[str] = set()

    for location, platform in rows_query:
        platform_code = platform.code
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        if platform_code == MAP_HISTORIC_PLATFORM:
            if not is_russian_historic(location, live_locations, identity_key):
                continue
            # Одна и та же parkrun-площадка лежит в базе под двумя написаниями
            # слага (angarskie-prudy и angarskieprudy) — 46 таких пар. Обе ведут
            # на одну страницу локации, поэтому в каталоге показываем одну
            # строку. Сами дубли этим не лечатся: события у них разрезаны между
            # строками, это отдельная работа по данным.
            if identity_key in historic_seen:
                continue
            historic_seen.add(identity_key)
        else:
            live_locations.setdefault(identity_key, location)
        country, city = inherited_geo_fields(location, live_locations.get(identity_key))
        latitude, longitude = catalog_index.coordinates_for(location, platform_code)
        visits_by_platform = visit_index.get(identity_key) or {}
        visit = _earliest_visit(visits_by_platform)
        first_visit = visit[0] if visit is not None else None
        first_visit_platform = visit[1] if visit is not None else None
        rows.append(
            {
                "row_key": f"{location.id}:{platform_code}",
                "catalog_identity_key": identity_key,
                "location_id": str(location.id),
                "location_slug": location.external_key.strip().lower(),
                "name": catalog_index.display_name(location, platform_code),
                "city": city,
                "region": location.region,
                "country": country,
                "platform_code": platform_code,
                "is_paused": _location_is_paused(location, catalog_index, platform_code),
                "is_cancelled": _location_is_cancelled(location, platform_code),
                # Как на карте: у parkrun-строк своих координат нет, но связанные с
                # действующей локацией берут её точку.
                "has_coordinates": latitude is not None and longitude is not None,
                "location_url": location_page_url(platform_code, location.external_key, location.source_url),
                "visited": first_visit is not None,
                "first_visit_date": first_visit.isoformat() if first_visit is not None else None,
                # В какой системе был самый ранний визит: у строки 5 вёрст это
                # может быть parkrun — тогда показываем это рядом с датой.
                "first_visit_platform": first_visit_platform,
                # Разбивка по системам — фронт пересчитывает отметку под фильтр
                # систем (сузили до 5 вёрст → parkrun-визит не в счёт).
                "visits_by_platform": {
                    code: visit_date.isoformat() for code, visit_date in sorted(visits_by_platform.items())
                },
            }
        )

    rows.sort(key=lambda item: (str(item["name"]).lower(), str(item["platform_code"])))
    return {
        "rows": rows,
        "total_rows": len(rows),
    }
