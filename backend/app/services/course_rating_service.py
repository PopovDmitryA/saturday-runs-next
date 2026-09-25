"""Рейтинги трасс: перепад высот, прямолинейность и пятачок.

Обе метрики берутся из паспорта трассы локации. Локации, где треков ещё
недостаточно, из рейтинга не выкидываются — они показываются серыми: так
видно, где дыры, и куда нужны треки.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Event, Location, LocationCourseProfile, Platform
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_course_service import has_enough_data

# Локация считается живой, если за это время у неё был хоть один старт:
# закрытые площадки в рейтинге трасс не нужны.
ACTIVE_WINDOW_DAYS = 400
# Треки мы собираем у своих участников, а у parkrun в каталоге полторы тысячи
# зарубежных площадок — в рейтинге российских трасс им делать нечего.
RUSSIA_COUNTRY = "Россия"

METRICS = {
    # Перепад высот: разница низшей и высшей точки. Надёжнее набора — набор у
    # барометра пляшет от погоды (на одной трассе 7–42 м за разные субботы).
    "elevation": ("elevation_span_m", True),
    # Прямолинейность: суммарный поворот за круг дистанции, меньше — прямее.
    "straightness": ("turn_sum_deg", False),
    # Пятачок: площадь самого тесного прямоугольника вокруг трассы. Меньше —
    # теснее намотано, и именно это интересно: пять километров на клочке земли.
    "footprint": ("box_area_m2", False),
}


def list_course_ratings(db: Session, metric: str) -> list[dict[str, Any]]:
    """Строки рейтинга: локации с данными сверху, серые — следом."""
    field, descending = METRICS[metric]

    profiles = {
        profile.location_id: profile
        for profile in db.scalars(
            select(LocationCourseProfile).where(LocationCourseProfile.is_current.is_(True))
        )
    }

    since = date.today() - timedelta(days=ACTIVE_WINDOW_DAYS)
    rows = db.execute(
        select(Location, Platform.code, func.max(Event.event_date))
        .join(Platform, Location.platform_id == Platform.id)
        .join(Event, Event.location_id == Location.id)
        .where(
            Event.is_test_event.is_(False),
            # Зарубежные parkrun отсекаем. У 5 вёрст, S95 и RunPark страна
            # почти всегда Россия и иногда пуста — их пустую страну считаем
            # своей; у parkrun пустая страна означает зарубежную площадку из
            # общего каталога, такие в рейтинг не идут.
            (Location.country == RUSSIA_COUNTRY)
            | (Location.country.is_(None) & (Platform.code != "parkrun")),
        )
        .group_by(Location.id, Platform.code)
        .having(func.max(Event.event_date) >= since)
    ).all()

    catalog_index = LocationCatalogIndex(db)
    items: list[dict[str, Any]] = []
    for location, platform_code, last_event in rows:
        profile = profiles.get(location.id)
        ready = has_enough_data(profile)
        value = getattr(profile, field) if profile is not None else None
        shown = profile if (ready and profile is not None) else None
        items.append(
            {
                "location_slug": location.external_key.strip().lower(),
                "location_name": catalog_index.display_name(location, platform_code),
                "city": location.city,
                "region": location.region,
                "platform_code": platform_code,
                "last_event_date": last_event,
                # has_data=false — локация в рейтинге есть, но цифр пока нет.
                "has_data": ready,
                "value": value if ready else None,
                "tracks_count": profile.tracks_count if profile else 0,
                "unique_user_count": profile.unique_user_count if profile else 0,
                "distance_m": shown.distance_m if shown else None,
                "elevation_span_m": shown.elevation_span_m if shown else None,
                "elevation_gain_m": shown.elevation_gain_m if shown else None,
                "turn_sum_deg": shown.turn_sum_deg if shown else None,
                "longest_straight_m": shown.longest_straight_m if shown else None,
                "lap_count": shown.lap_count if shown else None,
                "box_short_m": shown.box_short_m if shown else None,
                "box_long_m": shown.box_long_m if shown else None,
                "box_area_m2": shown.box_area_m2 if shown else None,
            }
        )

    with_data = [item for item in items if item["has_data"] and item["value"] is not None]
    without_data = [item for item in items if not (item["has_data"] and item["value"] is not None)]
    with_data.sort(key=lambda item: item["value"], reverse=descending)
    without_data.sort(key=lambda item: item["location_name"])

    for position, item in enumerate(with_data, start=1):
        item["position"] = position
    for item in without_data:
        item["position"] = None
    return with_data + without_data
