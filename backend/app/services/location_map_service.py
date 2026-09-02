from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.location_page_url import location_page_url, pick_primary_location_url
from app.models import Location, Platform
from app.services.location_catalog_service import LocationCatalogIndex, normalize_platform_code
from app.services.user_unique_locations_detail import build_user_unique_location_details

# Системы с действующими точками. parkrun стоит отдельно: он ушёл из России в
# 2022, его локации показываем как историю (см. MAP_HISTORIC_PLATFORM).
MAP_LIVE_PLATFORMS = ("five_verst", "s95", "runpark")
MAP_HISTORIC_PLATFORM = "parkrun"
MAP_PLATFORMS = (*MAP_LIVE_PLATFORMS, MAP_HISTORIC_PLATFORM)
# В базе 2572 parkrun-локации со всего мира — на нашей карте нужны только
# российские. Страна пишется двумя вариантами, оба исторические.
RU_COUNTRY_NAMES = ("Россия", "Russia")


def map_location_filter() -> ColumnElement[bool]:
    """Условие отбора локаций для карты и каталога.

    У действующих систем ориентир — is_official_map. parkrun тащим целиком:
    страна у его строк почти всегда стаб «United Kingdom» (локации приезжали из
    мирового каталога), поэтому по ней отбирать нельзя — из 150 сцепленных с
    нашими локациями 102 помечены Британией. Отсев делает is_russian_historic
    уже по связке.
    """
    return or_(
        Platform.code.in_(MAP_LIVE_PLATFORMS) & Location.is_official_map.is_(True),
        Platform.code == MAP_HISTORIC_PLATFORM,
    )


def is_russian_historic(location: Location, live_identities: dict[str, Location], identity_key: str) -> bool:
    """Нужна ли эта parkrun-локация на нашей карте.

    Основной признак — связка с действующей локацией: если парк живёт в 5 вёрстах
    или s95, то и его parkrun-прошлое наше. Собственная страна строки — запасной
    вариант для тех, у кого преемника не осталось (Шуваловский, Улица Голубая).
    """
    if identity_key in live_identities:
        return True
    return (location.country or "") in RU_COUNTRY_NAMES


def inherited_geo_fields(
    location: Location, live: Location | None
) -> tuple[str | None, str | None]:
    """Страна и город для исторической строки: у связки они верные, у своей — стаб."""
    if live is not None:
        return (live.country or location.country, live.city or location.city)
    return (location.country, location.city)


def map_platform_order() -> object:
    """parkrun — последним. Точку создаёт первая строка идентичности, а
    сортировка по коду ставила бы «parkrun» перед «s95»: живая локация получала
    бы от исторической строки статус «отменена» и английское имя.
    """
    return case((Platform.code == MAP_HISTORIC_PLATFORM, 1), else_=0)


def _collect_dates(platforms: list[dict[str, object]], field: str) -> list[str]:
    values: set[str] = set()
    for platform in platforms:
        for value in platform.get(field, []):
            values.add(str(value))
    return sorted(values, reverse=True)


def _location_is_paused(location: Location, catalog_index: LocationCatalogIndex, platform_code: str) -> bool:
    if platform_code == MAP_HISTORIC_PLATFORM:
        # Российский parkrun не работает с 2022 года, и это ровно «не
        # действует»: отдельного правила ему больше не нужно — общий признак
        # ставит правило молчания (решение Дмитрия 20.08.2026).
        return True
    # Для каталожных узлов пауза считается по всем платформам сразу
    # (см. LocationCatalogIndex._catalog_is_paused), а не по одной строке.
    return catalog_index.is_paused(location, platform_code)


def _catalog_active_platform(
    catalog_index: LocationCatalogIndex, location: Location, platform_code: str
) -> str | None:
    """Код системы, в которой площадка работает сейчас (по узлу каталога)."""
    catalog = catalog_index.get_for_location(location, platform_code)
    if catalog is None:
        return None
    return normalize_platform_code(catalog.active_platform)


def _location_is_cancelled(location: Location, platform_code: str | None = None) -> bool:
    """Отмена ближайшего старта — временный статус.

    Приходит из реестра системы («(отмена)» у 5 вёрст) и снимается следующим
    синком. С «не действует» не пересекается: площадка работает, просто в
    ближайшую субботу не побегут. До 20.08.2026 сюда же попадал весь российский
    parkrun — теперь он честно «не действует» (см. _location_is_paused).
    """
    return bool(location.is_cancelled)


def _location_is_upcoming(location: Location, platform_code: str | None = None) -> bool:
    """Площадка объявлена, но ещё не стартовала."""
    if platform_code == MAP_HISTORIC_PLATFORM:
        return False
    return bool(location.is_upcoming)


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
        .filter(map_location_filter())
        .order_by(map_platform_order(), Platform.code.asc(), Location.name.asc())
        .all()
    )

    catalog_index = LocationCatalogIndex(db)
    buckets: dict[str, dict[str, object]] = {}
    # Действующие локации по идентичности: parkrun-строки идут после них
    # (см. map_platform_order) и наследуют отсюда страну, город и координаты.
    live_locations: dict[str, Location] = {}
    official_total = 0
    unmapped = 0

    for location, platform in rows:
        identity_key = catalog_index.canonical_identity_key(location, platform.code)
        if platform.code == MAP_HISTORIC_PLATFORM:
            if not is_russian_historic(location, live_locations, identity_key):
                continue
        else:
            live_locations.setdefault(identity_key, location)
        official_total += 1
        # Координаты берём через каталог: у parkrun-строк своих нет ни у одной,
        # но 46 из 55 связаны с действующей локацией и получают её точку.
        # Оставшиеся девять пока без координат — они честно уходят в unmapped и
        # появятся на карте, как только координаты проставят.
        latitude, longitude = catalog_index.coordinates_for(location, platform.code)
        if latitude is None or longitude is None:
            unmapped += 1
            continue

        bucket = buckets.get(identity_key)
        if bucket is None:
            bucket = {
                "id": str(location.id),
                "catalog_identity_key": identity_key,
                "location_slug": location.external_key.strip().lower(),
                "name": catalog_index.display_name(location, platform.code),
                "latitude": latitude,
                "longitude": longitude,
                "city": location.city,
                "region": location.region,
                "platform_codes": set(),
                "platform_urls": {},
                "active_platform": platform.code,
                # Действующая система узла каталога — она же цвет точки на карте.
                "catalog_active_platform": _catalog_active_platform(
                    catalog_index, location, platform.code
                ),
                "is_paused": _location_is_paused(location, catalog_index, platform.code),
                "is_cancelled": _location_is_cancelled(location, platform.code),
                "is_upcoming": _location_is_upcoming(location, platform.code),
                "run_count": 0,
                "volunteer_count": 0,
                "visit_count": 0,
                "last_visit_date": None,
                "run_dates": [],
                "volunteer_dates": [],
                "platform_visits": [],
            }
            buckets[identity_key] = bucket
        elif platform.code == MAP_HISTORIC_PLATFORM:
            # Историческая связка добавляет точке только сам факт «здесь был
            # parkrun»: имя, регион и статус остаются от действующих систем.
            # Иначе OR ниже пометил бы отменённой живую локацию, где parkrun
            # бегали до 2022 (Чертаново, Сокольники и ещё 44 такие же).
            pass
        else:
            if len(catalog_index.display_name(location, platform.code)) > len(str(bucket["name"])):
                bucket["name"] = catalog_index.display_name(location, platform.code)
            if location.region and not bucket.get("region"):
                bucket["region"] = location.region
            # Площадка не действует, только если не действует ВЕЗДЕ: парк,
            # ушедший из одной системы в другую, живой. Отмена наоборот —
            # достаточно одной системы, там в субботу не побегут.
            bucket["is_paused"] = bool(bucket["is_paused"]) and _location_is_paused(
                location,
                catalog_index,
                platform.code,
            )
            bucket["is_cancelled"] = bool(bucket.get("is_cancelled")) or _location_is_cancelled(
                location, platform.code
            )
            bucket["is_upcoming"] = bool(bucket.get("is_upcoming")) or _location_is_upcoming(
                location, platform.code
            )

        platform_codes: set[str] = bucket["platform_codes"]  # type: ignore[assignment]
        platform_codes.add(platform.code)
        page_url = location_page_url(platform.code, location.external_key, location.source_url)
        if page_url:
            urls: dict[str, str] = bucket["platform_urls"]  # type: ignore[assignment]
            urls[platform.code] = page_url

    points: list[dict[str, object]] = []
    for bucket in buckets.values():
        platform_codes = sorted(bucket["platform_codes"])  # type: ignore[arg-type]
        # Действующая система: сперва то, что говорит каталог, и лишь потом
        # «единственная платформа». Раньше правило было только вторым, и парк,
        # переживший parkrun и уехавший в 5 вёрст или S95 (Тверь, Подольск,
        # Великий Новгород и ещё десятки), оставался без системы вовсе — на
        # карте такая точка красилась серым, как «не действует»
        # (Дмитрий 02.09.2026).
        catalog_active = bucket.get("catalog_active_platform")
        if catalog_active and str(catalog_active) in platform_codes:
            active = str(catalog_active)
        else:
            active = platform_codes[0] if len(platform_codes) == 1 else None
        platform_urls: dict[str, str] = bucket.get("platform_urls") or {}  # type: ignore[assignment]
        point = {
            key: value
            for key, value in bucket.items()
            if key not in {"platform_codes", "platform_urls", "catalog_active_platform"}
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
