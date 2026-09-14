"""Рейтинг «Погода»: моржи, суровые локации и температура против результата.

Три витрины на одной странице /ratings/weather (все считаются из start_weather
и протоколов, кэш на сутки, прогрев вместе с лидербордами):

* «Моржи» — участники по числу стартов при −20° и ниже (и их самый холодный
  старт); рядом «самые холодные финиши» — просто топ по температуре.
* «Суровые локации» — площадки по медианной температуре зимних стартов
  (декабрь–февраль, минимум 3 старта) и симметрично самые жаркие летом.
* «Температура и результат» — по всем финишам страны: среднее время и средняя
  явка в корзинах по 5°, откуда видно «идеальную температуру для 5 км».
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any, cast
from uuid import UUID

import redis
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Event, Location, Platform, PlatformLink, RunResult, StartWeather
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.start_weather_service import DEEP_FROST_C, format_temperature, weather_brief

RATING_CACHE_KEY = "weather-rating:v1"
RATING_CACHE_TTL_SECONDS = 24 * 60 * 60
WINTER_MONTHS = (12, 1, 2)
SUMMER_MONTHS = (6, 7, 8)
MIN_SEASON_STARTS = 3
TOP_LIMIT = 30
BUCKET_C = 5


def _walruses(db: Session) -> dict[str, Any]:
    from app.services.location_page_service import _participant_display_names

    rows = (
        db.query(
            RunResult.participant_id,
            Platform.code,
            Event.event_date,
            Location,
            StartWeather.temperature_c,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Location.id == Event.location_id)
        .join(Platform, Platform.id == Event.platform_id)
        .join(
            StartWeather,
            (StartWeather.location_id == Event.location_id) & (StartWeather.obs_date == Event.event_date),
        )
        .filter(
            Event.is_test_event.is_(False),
            RunResult.finish_time_sec.isnot(None),
            StartWeather.temperature_c <= DEEP_FROST_C,
        )
        .all()
    )
    catalog_index = LocationCatalogIndex(db)
    # Один человек с привязанными профилями двух систем — одна строка: parkrun-
    # зимы Якутска и 5 вёрст после него складываются. Без привязки участники
    # разных систем остаются отдельными строками, как в остальных рейтингах.
    participant_ids = {row[0] for row in rows}
    user_of: dict[UUID, UUID] = {
        participant_id: user_id
        for participant_id, user_id in db.query(PlatformLink.participant_id, PlatformLink.user_id)
        .filter(PlatformLink.participant_id.in_(list(participant_ids)))
        .all()
    }
    per_participant: dict[UUID, dict[str, Any]] = {}
    key_participant: dict[UUID, UUID] = {}
    for participant_id, code, when, location, temp in rows:
        key = user_of.get(participant_id, participant_id)
        key_participant.setdefault(key, participant_id)
        entry = per_participant.setdefault(
            key,
            {"count": 0, "coldest_c": None, "coldest_date": None, "coldest_location": None, "platform_code": code},
        )
        entry["count"] += 1
        value = float(temp)
        if entry["coldest_c"] is None or value < entry["coldest_c"]:
            entry["coldest_c"] = value
            entry["coldest_date"] = when.isoformat()
            entry["coldest_location"] = catalog_index.display_name(location, code)
            entry["platform_code"] = code
    names = _participant_display_names(db, key_participant.values())
    items = []
    for key, entry in per_participant.items():
        name, handle = names.get(key_participant[key], (None, None))
        items.append(
            {
                "name": name or "Участник",
                "handle": handle,
                "platform_code": entry["platform_code"],
                "count": entry["count"],
                "coldest_c": entry["coldest_c"],
                "coldest_label": format_temperature(entry["coldest_c"]),
                "coldest_date": entry["coldest_date"],
                "coldest_location": entry["coldest_location"],
            }
        )
    by_count = sorted(items, key=lambda item: (-item["count"], item["coldest_c"]))[:TOP_LIMIT]
    by_cold = sorted(items, key=lambda item: (item["coldest_c"], -item["count"]))[:TOP_LIMIT]
    for place, item in enumerate(by_count, start=1):
        item["place"] = place
    coldest = [dict(item, place=place) for place, item in enumerate(by_cold, start=1)]
    return {"by_count": by_count, "coldest": coldest, "participants": len(items), "threshold_c": DEEP_FROST_C}


def _season_locations(db: Session) -> dict[str, Any]:
    rows = (
        db.query(Location, Platform.code, Event.event_date, StartWeather.temperature_c)
        .join(Event, Event.location_id == Location.id)
        .join(Platform, Platform.id == Event.platform_id)
        .join(
            StartWeather,
            (StartWeather.location_id == Event.location_id) & (StartWeather.obs_date == Event.event_date),
        )
        .filter(Event.is_test_event.is_(False), StartWeather.temperature_c.isnot(None))
        .all()
    )
    catalog_index = LocationCatalogIndex(db)
    winter: dict[str, list[float]] = defaultdict(list)
    summer: dict[str, list[float]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}
    seen_dates: set[tuple[str, str]] = set()
    for location, code, when, temp in rows:
        key = catalog_index.canonical_identity_key(location, code)
        if (key, when.isoformat()) in seen_dates:
            continue
        seen_dates.add((key, when.isoformat()))
        meta.setdefault(
            key,
            {
                "name": catalog_index.display_name(location, code),
                "slug": location.external_key.strip().lower(),
                "platform_code": code,
                "region": location.region,
            },
        )
        if when.month in WINTER_MONTHS:
            winter[key].append(float(temp))
        if when.month in SUMMER_MONTHS:
            summer[key].append(float(temp))

    def table(source: dict[str, list[float]], *, reverse: bool) -> list[dict[str, Any]]:
        items = [
            {
                **meta[key],
                "starts": len(values),
                "median_c": round(statistics.median(values), 1),
                "min_c": round(min(values), 1),
                "max_c": round(max(values), 1),
            }
            for key, values in source.items()
            if len(values) >= MIN_SEASON_STARTS
        ]
        items.sort(key=lambda item: item["median_c"], reverse=reverse)
        return [dict(item, place=place) for place, item in enumerate(items[:TOP_LIMIT], start=1)]

    return {"coldest_winter": table(winter, reverse=False), "hottest_summer": table(summer, reverse=True)}


def _temperature_buckets(db: Session) -> list[dict[str, Any]]:
    bucket = func.floor(StartWeather.temperature_c / BUCKET_C) * BUCKET_C
    rows = (
        db.query(
            bucket.label("bucket"),
            func.count(RunResult.id),
            func.avg(RunResult.finish_time_sec),
            func.count(func.distinct(Event.id)),
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(
            StartWeather,
            (StartWeather.location_id == Event.location_id) & (StartWeather.obs_date == Event.event_date),
        )
        .filter(
            Event.is_test_event.is_(False),
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
            StartWeather.temperature_c.isnot(None),
        )
        .group_by("bucket")
        .order_by("bucket")
        .all()
    )
    result: list[dict[str, Any]] = []
    for bucket_value, finishes, avg_sec, events in rows:
        low = int(bucket_value)
        if finishes < 200:
            continue
        result.append(
            {
                "from_c": low,
                "to_c": low + BUCKET_C,
                "label": f"{format_temperature(low)}…{format_temperature(low + BUCKET_C)}",
                "finishes": int(finishes),
                "starts": int(events),
                "avg_finish_sec": int(round(float(avg_sec))) if avg_sec is not None else None,
                "avg_finishers": round(int(finishes) / int(events), 1) if events else None,
            }
        )
    return result


def _extremes(db: Session) -> dict[str, Any]:
    """Самый холодный и самый жаркий старт в истории — по всем локациям."""

    catalog_index = LocationCatalogIndex(db)

    def pick(order: Any) -> dict[str, Any] | None:
        row = (
            db.query(StartWeather, Location, Platform.code, Event.finishers_count, Event.event_number)
            .join(Event, (Event.location_id == StartWeather.location_id) & (Event.event_date == StartWeather.obs_date))
            .join(Location, Location.id == Event.location_id)
            .join(Platform, Platform.id == Event.platform_id)
            .filter(Event.is_test_event.is_(False), StartWeather.temperature_c.isnot(None))
            .order_by(order)
            .first()
        )
        if row is None:
            return None
        weather, location, code, finishers, number = row
        return {
            "location_name": catalog_index.display_name(location, code),
            "location_slug": location.external_key.strip().lower(),
            "platform_code": code,
            "finishers": finishers,
            "event_number": number,
            "weather": weather_brief(weather),
        }

    return {
        "coldest": pick(StartWeather.temperature_c.asc()),
        "hottest": pick(StartWeather.temperature_c.desc()),
        "wettest": pick(StartWeather.precipitation_run_mm.desc().nullslast()),
        "windiest": pick(StartWeather.wind_gusts_ms.desc().nullslast()),
    }


def _compute(db: Session) -> dict[str, Any]:
    return {
        "walruses": _walruses(db),
        "locations": _season_locations(db),
        "temperature": _temperature_buckets(db),
        "extremes": _extremes(db),
    }


def _read_cache() -> dict[str, Any] | None:
    try:
        raw = get_redis_client().get(RATING_CACHE_KEY)
    except redis.RedisError:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return cast(dict[str, Any], json.loads(raw))
    except (TypeError, ValueError):
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    try:
        get_redis_client().setex(RATING_CACHE_KEY, RATING_CACHE_TTL_SECONDS, json.dumps(payload, default=str))
    except redis.RedisError:
        pass


def build_weather_rating(db: Session, *, use_cache: bool = True, refresh: bool = False) -> dict[str, Any]:
    if use_cache and not refresh:
        cached = _read_cache()
        if cached is not None:
            return cached
    payload = _compute(db)
    if use_cache:
        _write_cache(payload)
    return payload


def refresh_weather_rating_cache(db: Session) -> int:
    payload = build_weather_rating(db, refresh=True)
    return len(payload["walruses"]["by_count"])
