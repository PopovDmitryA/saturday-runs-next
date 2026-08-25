"""Огрублённая отметка «где был участник, когда открывал карту».

Пишется по одной строке в сутки на учётную запись и только для тех, кто сам
разрешил браузеру определять положение (иначе координат у нас просто нет).
Точность — два знака после запятой, клетка примерно километр на километр:
этого хватает на оба вопроса, ради которых отметки и собираются —

* в каких городах есть участники, но нет площадки поблизости;
* верно ли сайт угадывает домашнюю локацию (она выбирается автоматически, по
  числу пробежек, и человеку про это честно написано «выбрана автоматически»).

Точнее хранить незачем: на километровой клетке дом и работа обычно неразличимы,
а оба вопроса решаются одинаково хорошо.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import cast

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import User, UserGeoPing
from app.services.home_distance_service import haversine_km, round_km
from app.services.home_location_service import (
    home_location_candidates_from_detail,
    resolve_home_location_from_candidates,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import list_catalog_map_locations
from app.services.user_unique_locations_detail import build_user_unique_location_details

logger = logging.getLogger(__name__)

# Два знака после запятой — примерно километр по широте.
COORDINATE_PRECISION = 2

# Отметки с погрешностью хуже этого не пишем: определение по вышкам сотовой сети
# на таком разбросе не говорит даже про город.
MAX_ACCURACY_M = 20_000


def _round_coordinate(value: float) -> float:
    return round(value, COORDINATE_PRECISION)


def record_geo_ping(
    db: Session,
    user: User,
    *,
    latitude: float,
    longitude: float,
    accuracy_m: float | None = None,
    today: date | None = None,
) -> bool:
    """Записать отметку. False — отметка отброшена (грубое определение) либо на
    сегодня она уже есть.

    Ничего не бросает наружу: это фоновая запись на обочине пользовательского
    действия, и падать из-за неё карта не должна.
    """
    if accuracy_m is not None and accuracy_m > MAX_ACCURACY_M:
        return False
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return False

    today = today or date.today()
    # Отметка на сегодня уже есть — уходим до тяжёлой части: и поиск ближайшей
    # площадки, и разбор домашней локации стоят по полному проходу по данным,
    # а вставка всё равно была бы отброшена уникальным индексом.
    already = (
        db.query(UserGeoPing.id)
        .filter(UserGeoPing.user_id == user.id, UserGeoPing.observed_on == today)
        .first()
    )
    if already is not None:
        return False

    # Округляем ещё раз, хотя фронт уже прислал огрублённое: точные координаты
    # не должны попадать в базу даже если запрос придёт мимо нашего кода.
    point = (_round_coordinate(latitude), _round_coordinate(longitude))

    nearest_key, nearest_km = _nearest_location(db, point)
    home_key, home_km = _home_and_distance(db, user, point)

    statement = (
        pg_insert(UserGeoPing)
        .values(
            user_id=user.id,
            observed_on=today,
            latitude=point[0],
            longitude=point[1],
            accuracy_m=int(accuracy_m) if accuracy_m is not None else None,
            nearest_identity_key=nearest_key,
            nearest_distance_km=nearest_km,
            home_identity_key=home_key,
            home_distance_km=home_km,
        )
        # Первая отметка дня остаётся, остальные молча отбрасываются — это и
        # есть обещанное «не чаще раза в сутки». Проверка выше ловит почти все
        # повторы, этот индекс страхует от гонки двух одновременных запросов.
        .on_conflict_do_nothing(constraint="uq_user_geo_pings_user_day")
        # RETURNING, а не rowcount: у такой вставки rowcount приходит −1, и по
        # нему нельзя отличить «записали» от «отбросили».
        .returning(UserGeoPing.id)
    )
    inserted = db.execute(statement).scalar_one_or_none()
    db.commit()
    return inserted is not None


def _nearest_location(
    db: Session, point: tuple[float, float]
) -> tuple[str | None, float | None]:
    """Ближайшая площадка каталога и расстояние до неё по прямой."""
    payload = list_catalog_map_locations(db)
    best_key: str | None = None
    best_km: float | None = None
    for item in cast(list[dict[str, object]], payload["points"]):
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        if latitude is None or longitude is None:
            continue
        distance = haversine_km(point, (float(cast(float, latitude)), float(cast(float, longitude))))
        if best_km is None or distance < best_km:
            best_km = distance
            best_key = cast("str | None", item.get("catalog_identity_key"))
    return best_key, round_km(best_km) if best_km is not None else None


def _home_and_distance(
    db: Session, user: User, point: tuple[float, float]
) -> tuple[str | None, float | None]:
    """Домашняя локация участника и расстояние от неё до отметки.

    Дом храним вместе с отметкой, а не считаем задним числом: он меняется —
    и автоматически, с новыми пробежками, и руками.
    """
    detail = build_user_unique_location_details(db, user.id)
    candidates = home_location_candidates_from_detail(detail)
    home, _is_auto = resolve_home_location_from_candidates(candidates, user)
    if home is None:
        return None, None
    catalog_index = LocationCatalogIndex(db)
    latitude, longitude = catalog_index.coordinates_for_identity_key(home.catalog_identity_key)
    if latitude is None or longitude is None:
        return home.catalog_identity_key, None
    return home.catalog_identity_key, round_km(haversine_km((latitude, longitude), point))
