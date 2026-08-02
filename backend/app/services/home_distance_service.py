"""Дальность стартов от домашней локации.

Метрика (решение Дмитрия 02.08.2026): сумма расстояний до УНИКАЛЬНЫХ посещённых
площадок. Каждая площадка даёт свои километры один раз, сколько бы раз человек
туда ни ездил, — иначе рейтинг выигрывал бы тот, кто каждую субботу катается в
соседний город, а не тот, кто объехал страну. Рядом считаем «самый дальний
старт» — он идёт заголовком плитки.

Расстояние — по прямой (гаверсинус) между точками старта, не по дороге.

В зачёт идут только БЕГОВЫЕ визиты: домашняя локация тоже определяется по числу
пробежек, и «дальность стартов» про старты, а не про волонтёрские выезды.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

from sqlalchemy.orm import Session

from app.models import User
from app.services.home_location_service import (
    HomeLocationCandidate,
    home_location_candidates_from_detail,
    resolve_home_location_from_candidates,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import list_catalog_map_locations
from app.services.user_unique_locations_detail import build_user_unique_location_details

EARTH_RADIUS_KM = 6371.0088

# Домашняя локация считается неочевидной, если вторая по числу пробежек площадка
# набрала не меньше этой доли от первой: авто-выбор «где больше пробежек» при
# 12 против 11 — это подбрасывание монетки, и человеку стоит сказать об этом.
AMBIGUITY_RATIO = 0.7


def haversine_km(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    lat1, lon1 = math.radians(first[0]), math.radians(first[1])
    lat2, lon2 = math.radians(second[0]), math.radians(second[1])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    inner = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(inner))


def round_km(value: float) -> float:
    """Близкие площадки — с десятыми (0.8 км честнее, чем «1»), дальние — целыми."""
    return round(value, 1) if value < 10 else float(round(value))


@dataclass(frozen=True)
class DistanceRow:
    catalog_identity_key: str
    location_slug: str | None
    name: str
    city: str | None
    region: str | None
    distance_km: float | None
    run_count: int
    last_visit_date: str | None
    is_home: bool
    is_paused: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "catalog_identity_key": self.catalog_identity_key,
            "location_slug": self.location_slug,
            "name": self.name,
            "city": self.city,
            "region": self.region,
            "distance_km": self.distance_km,
            "run_count": self.run_count,
            "last_visit_date": self.last_visit_date,
            "is_home": self.is_home,
            "is_paused": self.is_paused,
        }


@dataclass(frozen=True)
class HomeSummary:
    catalog_identity_key: str
    location_slug: str | None
    name: str
    city: str | None
    region: str | None
    run_count: int
    is_auto: bool
    # "tie" — ничья по числу пробежек, "close" — вторая площадка близко.
    # None — выбор однозначен либо задан руками.
    ambiguity: str | None
    runner_up_name: str | None
    has_coordinates: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "catalog_identity_key": self.catalog_identity_key,
            "location_slug": self.location_slug,
            "name": self.name,
            "city": self.city,
            "region": self.region,
            "run_count": self.run_count,
            "is_auto": self.is_auto,
            "ambiguity": self.ambiguity,
            "runner_up_name": self.runner_up_name,
            "has_coordinates": self.has_coordinates,
        }


@dataclass(frozen=True)
class HomeDistanceOverview:
    home: HomeSummary | None
    total_distance_km: float
    farthest: DistanceRow | None
    visited_count: int
    counted_count: int
    unknown_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "home": self.home.as_dict() if self.home is not None else None,
            "total_distance_km": self.total_distance_km,
            "farthest": self.farthest.as_dict() if self.farthest is not None else None,
            "visited_count": self.visited_count,
            "counted_count": self.counted_count,
            "unknown_count": self.unknown_count,
        }


def _home_ambiguity(
    candidates: list[HomeLocationCandidate],
    home: HomeLocationCandidate,
    *,
    is_auto: bool,
) -> tuple[str | None, str | None]:
    """(вид неоднозначности, название конкурента). Выбранное руками не трогаем."""
    if not is_auto:
        return None, None
    ranked = sorted(
        (candidate for candidate in candidates if candidate.run_count > 0),
        key=lambda candidate: (-candidate.run_count, candidate.name.casefold()),
    )
    if len(ranked) < 2 or ranked[0].catalog_identity_key != home.catalog_identity_key:
        return None, None
    leader, runner_up = ranked[0], ranked[1]
    if runner_up.run_count == leader.run_count:
        return "tie", runner_up.name
    if runner_up.run_count >= leader.run_count * AMBIGUITY_RATIO:
        return "close", runner_up.name
    return None, None


def _visited_rows(
    detail: dict[str, object],
    home_key: str | None,
    home_coordinates: tuple[float, float] | None,
) -> list[DistanceRow]:
    rows: list[DistanceRow] = []
    for item in cast(list[dict[str, object]], detail["locations"]):
        run_count = int(cast(int, item["run_count"]))
        if run_count <= 0:
            continue
        identity_key = str(item["catalog_identity_key"])
        latitude = cast("float | None", item["latitude"])
        longitude = cast("float | None", item["longitude"])
        distance: float | None = None
        if identity_key == home_key:
            distance = 0.0
        elif home_coordinates is not None and latitude is not None and longitude is not None:
            distance = round_km(haversine_km(home_coordinates, (latitude, longitude)))
        rows.append(
            DistanceRow(
                catalog_identity_key=identity_key,
                location_slug=cast("str | None", item["location_slug"]),
                name=str(item["name"]),
                city=cast("str | None", item["city"]),
                region=cast("str | None", item["region"]),
                distance_km=distance,
                run_count=run_count,
                last_visit_date=cast("str | None", item["last_visit_date"]),
                is_home=identity_key == home_key,
                is_paused=bool(item["is_paused"]),
            )
        )
    rows.sort(key=lambda row: (row.distance_km is None, -(row.distance_km or 0.0), row.name.casefold()))
    return rows


def _summarize(rows: list[DistanceRow]) -> tuple[float, DistanceRow | None, int, int]:
    counted = [row for row in rows if row.distance_km is not None]
    total = round_km(sum(row.distance_km or 0.0 for row in counted))
    farthest = None
    for row in counted:
        if row.distance_km and (farthest is None or row.distance_km > (farthest.distance_km or 0)):
            farthest = row
    return total, farthest, len(counted), len(rows) - len(counted)


def _home_summary(
    candidates: list[HomeLocationCandidate],
    home: HomeLocationCandidate,
    *,
    is_auto: bool,
    home_row: DistanceRow | None,
    home_coordinates: tuple[float, float] | None,
) -> HomeSummary:
    ambiguity, runner_up = _home_ambiguity(candidates, home, is_auto=is_auto)
    return HomeSummary(
        catalog_identity_key=home.catalog_identity_key,
        location_slug=home_row.location_slug if home_row is not None else None,
        name=home.name,
        city=home.city,
        region=home.region,
        run_count=home.run_count,
        is_auto=is_auto,
        ambiguity=ambiguity,
        runner_up_name=runner_up,
        has_coordinates=home_coordinates is not None,
    )


def _home_coordinates(detail: dict[str, object], home_key: str) -> tuple[float, float] | None:
    for item in cast(list[dict[str, object]], detail["locations"]):
        if str(item["catalog_identity_key"]) != home_key:
            continue
        latitude = cast("float | None", item["latitude"])
        longitude = cast("float | None", item["longitude"])
        if latitude is None or longitude is None:
            return None
        return (latitude, longitude)
    return None


def _overview_from_detail(
    detail: dict[str, object], user: User
) -> tuple[HomeDistanceOverview, list[DistanceRow], tuple[float, float] | None]:
    candidates = home_location_candidates_from_detail(detail)
    home, is_auto = resolve_home_location_from_candidates(candidates, user)
    if home is None:
        empty = HomeDistanceOverview(
            home=None,
            total_distance_km=0.0,
            farthest=None,
            visited_count=0,
            counted_count=0,
            unknown_count=0,
        )
        return empty, [], None
    home_coordinates = _home_coordinates(detail, home.catalog_identity_key)
    rows = _visited_rows(detail, home.catalog_identity_key, home_coordinates)
    total, farthest, counted, unknown = _summarize(rows)
    home_row = next((row for row in rows if row.is_home), None)
    overview = HomeDistanceOverview(
        home=_home_summary(
            candidates,
            home,
            is_auto=is_auto,
            home_row=home_row,
            home_coordinates=home_coordinates,
        ),
        total_distance_km=total,
        farthest=farthest,
        visited_count=len(rows),
        counted_count=counted,
        unknown_count=unknown,
    )
    return overview, rows, home_coordinates


def build_home_distance_overview(
    db: Session,
    user: User,
    *,
    include_test_events: bool = False,
    catalog_index: LocationCatalogIndex | None = None,
    detail: dict[str, object] | None = None,
) -> HomeDistanceOverview:
    """Сводка для плитки на главной кабинета.

    detail передают, когда детализация локаций уже посчитана: она стоит двух
    запросов по всем пробежкам пользователя.
    """
    if detail is None:
        detail = build_user_unique_location_details(
            db,
            user.id,
            include_test_events=include_test_events,
            catalog_index=catalog_index,
        )
    overview, _rows, _home_coordinates = _overview_from_detail(detail, user)
    return overview


def build_home_distance_detail(
    db: Session,
    user: User,
    *,
    include_test_events: bool = False,
) -> dict[str, object]:
    """Две таблицы модалки: посещённые площадки с их вкладом в зачёт и серый
    список действующих российских площадок, где человек ещё не был."""
    catalog_index = LocationCatalogIndex(db)
    detail = build_user_unique_location_details(
        db,
        user.id,
        include_test_events=include_test_events,
        catalog_index=catalog_index,
    )
    overview, visited_rows, home_coordinates = _overview_from_detail(detail, user)
    visited_keys = {
        str(item["catalog_identity_key"]) for item in cast(list[dict[str, object]], detail["locations"])
    }
    return {
        **overview.as_dict(),
        "visited": [row.as_dict() for row in visited_rows],
        "unvisited": [
            row.as_dict() for row in _unvisited_rows(db, visited_keys, home_coordinates)
        ],
    }


def _unvisited_rows(
    db: Session,
    visited_keys: set[str],
    home_coordinates: tuple[float, float] | None,
) -> list[DistanceRow]:
    """Действующие российские площадки, где человек ещё не бегал.

    Набор — тот же, что на карте и в каталоге локаций (list_catalog_map_locations):
    закрытые и приостановленные площадки в список целей не идут, туда сейчас не
    поедешь.
    """
    payload = list_catalog_map_locations(db)
    rows: list[DistanceRow] = []
    for point in cast(list[dict[str, object]], payload["points"]):
        identity_key = str(point["catalog_identity_key"])
        if identity_key in visited_keys:
            continue
        if bool(point.get("is_cancelled")) or bool(point.get("is_paused")):
            continue
        latitude = cast("float | None", point.get("latitude"))
        longitude = cast("float | None", point.get("longitude"))
        distance: float | None = None
        if home_coordinates is not None and latitude is not None and longitude is not None:
            distance = round_km(haversine_km(home_coordinates, (latitude, longitude)))
        rows.append(
            DistanceRow(
                catalog_identity_key=identity_key,
                location_slug=cast("str | None", point.get("location_slug")),
                name=str(point["name"]),
                city=cast("str | None", point.get("city")),
                region=cast("str | None", point.get("region")),
                distance_km=distance,
                run_count=0,
                last_visit_date=None,
                is_home=False,
                is_paused=False,
            )
        )
    rows.sort(key=lambda row: (row.distance_km is None, row.distance_km or 0.0, row.name.casefold()))
    return rows


def location_distance_from_home(
    db: Session,
    user: User,
    identity_key: str,
    coordinates: tuple[float | None, float | None],
    *,
    include_test_events: bool = False,
    catalog_index: LocationCatalogIndex | None = None,
) -> dict[str, object] | None:
    """Плитка «сколько отсюда до дома» на странице локации.

    None — когда домашняя локация не определилась (у человека нет пробежек):
    показывать нечего, плитку на странице не рисуем.
    """
    detail = build_user_unique_location_details(
        db,
        user.id,
        include_test_events=include_test_events,
        catalog_index=catalog_index,
    )
    candidates = home_location_candidates_from_detail(detail)
    home, is_auto = resolve_home_location_from_candidates(candidates, user)
    if home is None:
        return None
    home_coordinates = _home_coordinates(detail, home.catalog_identity_key)
    by_key = {
        str(item["catalog_identity_key"]): item
        for item in cast(list[dict[str, object]], detail["locations"])
    }
    visited = by_key.get(identity_key)
    home_item = by_key.get(home.catalog_identity_key)
    latitude, longitude = coordinates
    distance: float | None = None
    if identity_key == home.catalog_identity_key:
        distance = 0.0
    elif home_coordinates is not None and latitude is not None and longitude is not None:
        distance = round_km(haversine_km(home_coordinates, (latitude, longitude)))
    run_count = int(cast(int, visited["run_count"])) if visited is not None else 0
    return {
        "distance_km": distance,
        "is_home": identity_key == home.catalog_identity_key,
        "visited": run_count > 0,
        "run_count": run_count,
        "home_name": home.name,
        "home_slug": cast("str | None", home_item["location_slug"]) if home_item is not None else None,
        "home_is_auto": is_auto,
    }
