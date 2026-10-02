"""Журнал «где ищут старт» и отчёт «белые пятна» для админки.

Каждая геопозиция, присланная боту, — строка nearby_query_log: клетка сетки,
ближайшая локация и расстояние до неё. Главный список отчёта — белые пятна:
клетки, где ищут субботний старт, а ближайшая локация дальше WHITE_SPOT_KM.
Это спрос на новые локации, и Дмитрию его больше негде увидеть.

Приватность: точка огрубляется до клетки CELL_DEGREES (~5 км по широте, 3 км
по долготе на широте Москвы) ДО записи — точных координат в базе нет. Ни
telegram_id, ни user_id не пишутся. Белому пятну подписывается место
(«Минск, Беларусь») обратным геокодингом центра клетки — фоном, задачей
nearby.label_place: Nominatim отвечает секунду, держать на нём ответ бота
нельзя.

Журнал — диагностика: его сбой не должен ломать ответ бота, поэтому запись
обёрнута в try/except.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NearbyQueryLog
from app.services.location_map_service import list_catalog_map_locations
from app.services.nearby_locations_service import NEARBY_RADIUS_KM

logger = logging.getLogger(__name__)

CELL_DEGREES = 0.05
# Белое пятно — ближайшая локация дальше получаса-сорока минут на машине.
WHITE_SPOT_KM = 30.0
SOURCES = ("bot", "inline")
TOP_LIMIT = 50


def cell_of(latitude: float, longitude: float) -> tuple[float, float]:
    """Узел сетки, к которому огрубляем точку."""
    return (
        round(round(latitude / CELL_DEGREES) * CELL_DEGREES, 2),
        round(round(longitude / CELL_DEGREES) * CELL_DEGREES, 2),
    )


def _known_label(db: Session, cell: tuple[float, float]) -> str | None:
    label = (
        db.query(NearbyQueryLog.place_label)
        .filter(
            NearbyQueryLog.cell_latitude == cell[0],
            NearbyQueryLog.cell_longitude == cell[1],
            NearbyQueryLog.place_label.isnot(None),
        )
        .limit(1)
        .scalar()
    )
    return str(label) if label else None


def record_nearby_query(
    db: Session,
    *,
    source: str,
    latitude: float,
    longitude: float,
    nearest_identity_key: str | None,
    nearest_distance_km: float | None,
    within_radius: int,
    is_linked: bool,
) -> int | None:
    """Записать запрос. Возвращает id строки, если белому пятну нужна подпись
    места (её ставит задача nearby.label_place), иначе None."""
    try:
        cell = cell_of(latitude, longitude)
        is_white_spot = nearest_distance_km is None or nearest_distance_km > WHITE_SPOT_KM
        label = _known_label(db, cell) if is_white_spot else None
        row = NearbyQueryLog(
            source=source if source in SOURCES else "bot",
            cell_latitude=cell[0],
            cell_longitude=cell[1],
            nearest_identity_key=nearest_identity_key,
            nearest_distance_km=nearest_distance_km,
            within_radius=within_radius,
            is_linked=is_linked,
            place_label=label,
        )
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001 — журнал не должен ронять ответ бота
        db.rollback()
        logger.warning("nearby query log failed", exc_info=True)
        return None
    return row.id if is_white_spot and label is None else None


# У городов-регионов (Минск) Nominatim отдаёт вместо области код ISO
# «BY-HM» — человеку он ничего не говорит.
_ISO_REGION = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")


def _place_label(address: dict[str, str | None]) -> str | None:
    parts = [address.get("city"), address.get("region"), address.get("country")]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen and not _ISO_REGION.match(part):
            seen.append(part)
    return ", ".join(seen)[:200] or None


def label_place(db: Session, row_id: int) -> str | None:
    """Подписать клетку белого пятна местом и разнести подпись по её строкам."""
    from app.geo.reverse_geocode import lookup_address

    row = db.get(NearbyQueryLog, row_id)
    if row is None:
        return None
    cell = (row.cell_latitude, row.cell_longitude)
    label = row.place_label or _known_label(db, cell)
    if label is None:
        label = _place_label(lookup_address(cell[0], cell[1]))
    if label is None:
        return None
    db.query(NearbyQueryLog).filter(
        NearbyQueryLog.cell_latitude == cell[0],
        NearbyQueryLog.cell_longitude == cell[1],
        NearbyQueryLog.place_label.is_(None),
    ).update({NearbyQueryLog.place_label: label}, synchronize_session=False)
    db.commit()
    return label


def _point_names(db: Session) -> dict[str, dict[str, Any]]:
    points = cast(list[dict[str, Any]], list_catalog_map_locations(db)["points"])
    return {
        str(point["catalog_identity_key"]): {"name": point.get("name"), "slug": point.get("location_slug")}
        for point in points
    }


def get_nearby_log_report(db: Session, *, period_days: int = 30) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    in_period = NearbyQueryLog.created_at >= since
    nothing_near = NearbyQueryLog.within_radius == 0

    total, nothing_near_total, linked_total, inline_total = (
        db.query(
            func.count(NearbyQueryLog.id),
            func.count(NearbyQueryLog.id).filter(nothing_near),
            func.count(NearbyQueryLog.id).filter(NearbyQueryLog.is_linked.is_(True)),
            func.count(NearbyQueryLog.id).filter(NearbyQueryLog.source == "inline"),
        )
        .filter(in_period)
        .one()
    )

    names = _point_names(db)

    def location_ref(identity_key: str | None) -> dict[str, Any]:
        info = names.get(identity_key or "") or {}
        return {"nearest_name": info.get("name"), "nearest_slug": info.get("slug")}

    cells = (
        db.query(
            NearbyQueryLog.cell_latitude,
            NearbyQueryLog.cell_longitude,
            func.max(NearbyQueryLog.place_label).label("place_label"),
            func.count(NearbyQueryLog.id).label("queries"),
            func.min(NearbyQueryLog.nearest_distance_km).label("distance"),
            func.max(NearbyQueryLog.nearest_identity_key).label("nearest_key"),
            func.max(NearbyQueryLog.created_at).label("last_at"),
        )
        .filter(in_period, NearbyQueryLog.nearest_distance_km > WHITE_SPOT_KM)
        .group_by(NearbyQueryLog.cell_latitude, NearbyQueryLog.cell_longitude)
        .all()
    )
    # Клетки одного места — одна строка: Минск ложится в несколько клеток
    # сетки, и «Минск 2 + Минск 1» читается хуже, чем «Минск 3». Без подписи
    # клетка остаётся сама по себе. Ссылка на карту — на самую частую клетку.
    spots: dict[object, dict[str, Any]] = {}
    for row in sorted(cells, key=lambda item: -int(item.queries)):
        key = row.place_label or (row.cell_latitude, row.cell_longitude)
        distance = float(row.distance) if row.distance is not None else None
        spot = spots.get(key)
        if spot is None:
            spots[key] = {
                "cell_latitude": row.cell_latitude,
                "cell_longitude": row.cell_longitude,
                "place_label": row.place_label,
                "count": int(row.queries),
                "nearest_distance_km": distance,
                "last_at": row.last_at,
                **location_ref(row.nearest_key),
            }
            continue
        spot["count"] += int(row.queries)
        spot["last_at"] = max(spot["last_at"], row.last_at)
        if distance is not None and (spot["nearest_distance_km"] is None or distance < spot["nearest_distance_km"]):
            spot["nearest_distance_km"] = distance
            spot.update(location_ref(row.nearest_key))
    white_spots = sorted(spots.values(), key=lambda spot: (-spot["count"], -spot["last_at"].timestamp()))[:TOP_LIMIT]

    top_nearest = [
        {"count": int(row.queries), **location_ref(row.nearest_key)}
        for row in db.query(
            NearbyQueryLog.nearest_identity_key.label("nearest_key"),
            func.count(NearbyQueryLog.id).label("queries"),
        )
        .filter(in_period, NearbyQueryLog.nearest_identity_key.isnot(None), ~nothing_near)
        .group_by(NearbyQueryLog.nearest_identity_key)
        .order_by(func.count(NearbyQueryLog.id).desc())
        .limit(TOP_LIMIT)
        .all()
    ]

    day = func.date(func.timezone("Europe/Moscow", NearbyQueryLog.created_at))
    daily = [
        {"day": row.day.isoformat(), "count": int(row.queries), "nothing_near": int(row.nothing_near)}
        for row in db.query(
            day.label("day"),
            func.count(NearbyQueryLog.id).label("queries"),
            func.count(NearbyQueryLog.id).filter(nothing_near).label("nothing_near"),
        )
        .filter(in_period)
        .group_by(day)
        .order_by(day.desc())
        .all()
    ]

    return {
        "period_days": period_days,
        "radius_km": NEARBY_RADIUS_KM,
        "white_spot_km": WHITE_SPOT_KM,
        "total": int(total),
        "nothing_near_total": int(nothing_near_total),
        "linked_total": int(linked_total),
        "inline_total": int(inline_total),
        "white_spots": white_spots,
        "top_nearest": top_nearest,
        "daily": daily,
    }
