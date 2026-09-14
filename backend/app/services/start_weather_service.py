"""Погода на стартах — чтение таблицы start_weather для сайта.

Сбор живёт в weather_service.py (Open-Meteo); здесь — всё, что показывает
собранное: строка «−27°, ясно, ветер 4 м/с» для протокола и поста, блок
«Погода на стартах» на странице локации, крайности недели для единого
протокола, личные итоги бегуна и сырьё для челленджей.

Договорённости о порогах (Дмитрий, 14.09.2026): дождь на старте — от 1 мм в
окне «старт−1ч…старт+2ч» (precipitation_run_mm), ливень — от 3 мм; сетка
Open-Meteo размазывает осадки, поэтому 0.3–1 мм — «возможно, моросило», а не
дождь. Мороз для челленджа «Морж» — от −20° по температуре воздуха в час
старта, жара для «Саламандры» — от +28°.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast
from uuid import UUID

import redis
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Event, Location, Platform, PlatformLink, RunResult, StartWeather
from app.services.location_catalog_service import LocationCatalogIndex

# --------------------------------------------------------------------------- пороги

RAIN_MM = 1.0
DOWNPOUR_MM = 3.0
DRIZZLE_MM = 0.3
FROST_C = -10.0
DEEP_FROST_C = -20.0
HEAT_C = 25.0
HOT_C = 28.0
SNOW_DEPTH_CM = 1.0
WINDY_GUST_MS = 15.0

SOURCE_FORECAST = "forecast"
PLATFORM_ORDER = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}

# Коды погоды WMO, как их отдаёт Open-Meteo, → подпись и значок.
_WMO_LABELS: dict[int, tuple[str, str]] = {
    0: ("ясно", "☀️"),
    1: ("малооблачно", "🌤️"),
    2: ("переменная облачность", "⛅"),
    3: ("пасмурно", "☁️"),
    45: ("туман", "🌫️"),
    48: ("изморозь", "🌫️"),
    51: ("слабая морось", "🌦️"),
    53: ("морось", "🌦️"),
    55: ("сильная морось", "🌧️"),
    56: ("ледяная морось", "🌧️"),
    57: ("ледяная морось", "🌧️"),
    61: ("небольшой дождь", "🌧️"),
    63: ("дождь", "🌧️"),
    65: ("сильный дождь", "🌧️"),
    66: ("ледяной дождь", "🌧️"),
    67: ("ледяной дождь", "🌧️"),
    71: ("небольшой снег", "🌨️"),
    73: ("снег", "🌨️"),
    75: ("сильный снег", "🌨️"),
    77: ("снежная крупа", "🌨️"),
    80: ("небольшой ливень", "🌧️"),
    81: ("ливень", "🌧️"),
    82: ("сильный ливень", "⛈️"),
    85: ("снегопад", "🌨️"),
    86: ("сильный снегопад", "🌨️"),
    95: ("гроза", "⛈️"),
    96: ("гроза с градом", "⛈️"),
    99: ("гроза с градом", "⛈️"),
}

RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}

MONTH_NAMES = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def weather_code_label(code: int | None) -> str:
    if code is None:
        return ""
    return _WMO_LABELS.get(int(code), ("", ""))[0]


def weather_code_icon(code: int | None) -> str:
    if code is None:
        return ""
    return _WMO_LABELS.get(int(code), ("", "🌡️"))[1]


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def format_temperature(value: float | None) -> str:
    """«−27°» / «0°» / «12°»: целые градусы, настоящий минус."""

    if value is None:
        return "—"
    # Половинки — от нуля (−43.5 → −44), а не банковское округление round():
    # так же считает formatTemp на фронте, и плитка сходится с summary.
    rounded = int(math.floor(abs(value) + 0.5)) * (-1 if value < 0 else 1)
    if rounded < 0:
        return f"−{abs(rounded)}°"
    return f"{rounded}°"


def is_rain(run_mm: float | None) -> bool:
    return run_mm is not None and run_mm >= RAIN_MM


def is_snow_cover(depth_cm: float | None) -> bool:
    return depth_cm is not None and depth_cm >= SNOW_DEPTH_CM


def rain_kind(run_mm: float | None) -> str:
    """dry / drizzle / rain / downpour по окну вокруг старта."""

    if run_mm is None or run_mm < DRIZZLE_MM:
        return "dry"
    if run_mm < RAIN_MM:
        return "drizzle"
    if run_mm < DOWNPOUR_MM:
        return "rain"
    return "downpour"


def weather_brief(row: StartWeather) -> dict[str, Any]:
    """Строка start_weather → компактный словарь для API + готовая строка summary."""

    temp = _f(row.temperature_c)
    apparent = _f(row.apparent_temperature_c)
    run_mm = _f(row.precipitation_run_mm)
    depth = _f(row.snow_depth_cm)
    wind = _f(row.wind_speed_ms)
    gusts = _f(row.wind_gusts_ms)
    code = int(row.weather_code) if row.weather_code is not None else None
    label = weather_code_label(code)

    parts: list[str] = [format_temperature(temp)]
    if label:
        parts.append(label)
    if wind is not None:
        parts.append("штиль" if wind < 1 else f"ветер {int(round(wind))} м/с")
    if is_rain(run_mm):
        parts.append(f"{run_mm:.1f} мм осадков")
    if is_snow_cover(depth):
        parts.append(f"снег {int(round(depth))} см")
    summary = ", ".join(parts)
    if temp is not None and apparent is not None and abs(apparent - temp) >= 4:
        summary += f" (ощущается {format_temperature(apparent)})"

    return {
        "date": row.obs_date.isoformat(),
        "start_time_local": row.start_time_local.strftime("%H:%M") if row.start_time_local else None,
        "temperature_c": temp,
        "apparent_temperature_c": apparent,
        "humidity_pct": row.humidity_pct,
        "weather_code": code,
        "label": label,
        "icon": weather_code_icon(code),
        "wind_speed_ms": wind,
        "wind_gusts_ms": gusts,
        "precipitation_run_mm": run_mm,
        "precipitation_before_mm": _f(row.precipitation_before_mm),
        "snowfall_cm": _f(row.snowfall_cm),
        "snow_depth_cm": depth,
        "day_temperature_min_c": _f(row.day_temperature_min_c),
        "day_temperature_max_c": _f(row.day_temperature_max_c),
        "day_precipitation_mm": _f(row.day_precipitation_mm),
        "sunrise_local": row.sunrise_local.strftime("%H:%M") if row.sunrise_local else None,
        "rain_kind": rain_kind(run_mm),
        "is_rain": is_rain(run_mm),
        "is_snow_cover": is_snow_cover(depth),
        "is_preliminary": row.source == SOURCE_FORECAST,
        "summary": summary,
    }


def weather_line(brief: dict[str, Any] | None) -> str | None:
    """Строка для текста поста: «🌤️ 12°, малооблачно, ветер 3 м/с»."""

    if not brief:
        return None
    icon = str(brief.get("icon") or "🌡️")
    return f"{icon} {brief['summary']}"


# --------------------------------------------------------------------------- точечное чтение


def weather_for_pairs(db: Session, pairs: Iterable[tuple[UUID, date]]) -> dict[tuple[UUID, date], dict[str, Any]]:
    """Погода по парам (локация, дата) одним запросом."""

    wanted = {(loc_id, when) for loc_id, when in pairs}
    if not wanted:
        return {}
    rows = (
        db.query(StartWeather).filter(tuple_(StartWeather.location_id, StartWeather.obs_date).in_(list(wanted))).all()
    )
    return {(row.location_id, row.obs_date): weather_brief(row) for row in rows}


def weather_rows_for_pairs(db: Session, pairs: Iterable[tuple[UUID, date]]) -> dict[tuple[UUID, date], StartWeather]:
    """Сырые строки по парам (локация, дата) — для расчётов, где нужны числа, а не summary."""

    wanted = {(loc_id, when) for loc_id, when in pairs}
    if not wanted:
        return {}
    rows = (
        db.query(StartWeather).filter(tuple_(StartWeather.location_id, StartWeather.obs_date).in_(list(wanted))).all()
    )
    return {(row.location_id, row.obs_date): row for row in rows}


def weather_for_event(db: Session, location_id: UUID, event_date: date) -> dict[str, Any] | None:
    return weather_for_pairs(db, [(location_id, event_date)]).get((location_id, event_date))


def _prefer_archive(rows: Iterable[StartWeather]) -> dict[date, StartWeather]:
    """Одна строка на дату: архив важнее предварительной, дубли площадок в двух системах схлопываются."""

    by_date: dict[date, StartWeather] = {}
    for row in rows:
        known = by_date.get(row.obs_date)
        if known is None or (known.source == SOURCE_FORECAST and row.source != SOURCE_FORECAST):
            by_date[row.obs_date] = row
    return by_date


# --------------------------------------------------------------------------- блок локации

LOCATION_WEATHER_CACHE_TTL_SECONDS = 3 * 60 * 60


def location_weather_cache_key(slug: str) -> str:
    return f"locations:weather:v1:{slug.strip().lower()}"


@dataclass(frozen=True)
class _StartInfo:
    event_id: UUID
    platform_code: str
    event_number: int | None
    finishers: int | None


def _starts_by_date(db: Session, location_ids: Sequence[UUID]) -> dict[date, _StartInfo]:
    """Дата → старт (номер, финишёры, система); один физический старт — одна запись."""

    from app.services.location_page_service import _dedupe_crosslinked_events

    rows = (
        db.query(Event.id, Event.event_date, Event.event_number, Event.finishers_count, Platform.code)
        .join(Platform, Platform.id == Event.platform_id)
        .filter(Event.location_id.in_(list(location_ids)), Event.is_test_event.is_(False))
        .all()
    )
    kept = _dedupe_crosslinked_events(db, [row[0] for row in rows])
    result: dict[date, _StartInfo] = {}
    for event_id, when, number, finishers, code in rows:
        if event_id not in kept:
            continue
        info = _StartInfo(event_id=event_id, platform_code=code, event_number=number, finishers=finishers)
        known = result.get(when)
        if known is None or PLATFORM_ORDER.get(code, 9) < PLATFORM_ORDER.get(known.platform_code, 9):
            result[when] = info
    return result


def _start_payload(info: _StartInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "platform_code": info.platform_code,
        "event_number": info.event_number,
        "finishers": info.finishers,
    }


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _month_rows(
    month: int, weather: dict[date, StartWeather], starts: dict[date, _StartInfo]
) -> tuple[list[StartWeather], str]:
    """Строки месяца: по стартам, а если стартов в этом месяце не было — по субботам."""

    on_starts = [
        row for when, row in weather.items() if when.month == month and when in starts and row.temperature_c is not None
    ]
    if on_starts:
        return on_starts, "starts"
    on_saturdays = [row for when, row in weather.items() if when.month == month and row.temperature_c is not None]
    return on_saturdays, "saturdays"


def _record(
    rows: list[StartWeather], starts: dict[date, _StartInfo], *, key: Any, reverse: bool = False
) -> dict[str, Any] | None:
    candidates = [row for row in rows if key(row) is not None]
    if not candidates:
        return None
    best = max(candidates, key=key) if reverse else min(candidates, key=key)
    return {"weather": weather_brief(best), "start": _start_payload(starts.get(best.obs_date))}


def _attendance_buckets(rows: list[StartWeather], starts: dict[date, _StartInfo]) -> list[dict[str, Any]]:
    """Средняя явка по погоде: сухо/дождь и по температурным корзинам."""

    def avg_finishers(selected: list[StartWeather]) -> tuple[int, float | None]:
        values = [
            float(starts[row.obs_date].finishers)  # type: ignore[arg-type]
            for row in selected
            if starts.get(row.obs_date) is not None and starts[row.obs_date].finishers
        ]
        return len(values), (round(statistics.mean(values), 1) if values else None)

    temp_buckets: list[tuple[str, str, float, float]] = [
        ("frost", "мороз ниже −10°", float("-inf"), -10.0),
        ("cold", "от −10° до 0°", -10.0, 0.0),
        ("cool", "от 0° до +10°", 0.0, 10.0),
        ("mild", "от +10° до +20°", 10.0, 20.0),
        ("warm", "теплее +20°", 20.0, float("inf")),
    ]
    result: list[dict[str, Any]] = []
    for key, label, low, high in temp_buckets:
        selected = [row for row in rows if row.temperature_c is not None and low <= float(row.temperature_c) < high]
        count, avg = avg_finishers(selected)
        if count:
            result.append({"key": key, "label": label, "starts": count, "avg_finishers": avg})
    dry = [row for row in rows if rain_kind(_f(row.precipitation_run_mm)) == "dry"]
    wet = [row for row in rows if is_rain(_f(row.precipitation_run_mm))]
    for key, label, selected in (("dry", "без дождя", dry), ("rain", "дождь на старте", wet)):
        count, avg = avg_finishers(selected)
        if count:
            result.append({"key": key, "label": label, "starts": count, "avg_finishers": avg})
    return result


def _compute_location_weather(db: Session, slug: str) -> dict[str, Any] | None:
    from app.services.location_page_service import resolve_location_identity

    identity = resolve_location_identity(db, slug)
    if identity is None:
        return None
    location_ids = [loc.id for loc, _code in identity.locations]
    weather = _prefer_archive(db.query(StartWeather).filter(StartWeather.location_id.in_(location_ids)).all())
    if not weather:
        return {
            "slug": identity.slug,
            "name": identity.name,
            "has_data": False,
            "months": [],
            "records": {},
            "latest": None,
            "years_ago": [],
            "attendance": [],
            "starts_with_weather": 0,
        }
    starts = _starts_by_date(db, location_ids)
    start_rows = [row for when, row in weather.items() if when in starts]

    months: list[dict[str, Any]] = []
    for month in range(1, 13):
        rows, basis = _month_rows(month, weather, starts)
        temps = [float(row.temperature_c) for row in rows]
        starts_in_month = sum(1 for when in starts if when.month == month and when in weather)
        if not rows:
            months.append(
                {
                    "month": month,
                    "label": MONTH_NAMES[month - 1],
                    "starts": starts_in_month,
                    "basis": "none",
                    "samples": 0,
                    "temperature_median_c": None,
                    "temperature_min_c": None,
                    "temperature_max_c": None,
                    "apparent_median_c": None,
                    "rain_share": None,
                    "snow_share": None,
                }
            )
            continue
        apparent = [float(row.apparent_temperature_c) for row in rows if row.apparent_temperature_c is not None]
        rain = sum(1 for row in rows if is_rain(_f(row.precipitation_run_mm)))
        snow = sum(
            1
            for row in rows
            if is_snow_cover(_f(row.snow_depth_cm))
            or (row.weather_code is not None and int(row.weather_code) in SNOW_CODES)
        )
        months.append(
            {
                "month": month,
                "label": MONTH_NAMES[month - 1],
                "starts": starts_in_month,
                "basis": basis,
                "samples": len(rows),
                "temperature_median_c": _median(temps),
                "temperature_min_c": round(min(temps), 1),
                "temperature_max_c": round(max(temps), 1),
                "apparent_median_c": _median(apparent),
                "rain_share": round(rain / len(rows), 2),
                "snow_share": round(snow / len(rows), 2),
            }
        )

    records = {
        "coldest": _record(start_rows, starts, key=lambda r: _f(r.temperature_c)),
        "hottest": _record(start_rows, starts, key=lambda r: _f(r.temperature_c), reverse=True),
        "wettest": _record(
            [r for r in start_rows if is_rain(_f(r.precipitation_run_mm))],
            starts,
            key=lambda r: _f(r.precipitation_run_mm),
            reverse=True,
        ),
        "windiest": _record(
            [r for r in start_rows if r.wind_gusts_ms is not None and float(r.wind_gusts_ms) >= 10],
            starts,
            key=lambda r: _f(r.wind_gusts_ms),
            reverse=True,
        ),
        "snowiest": _record(
            [r for r in start_rows if is_snow_cover(_f(r.snow_depth_cm))],
            starts,
            key=lambda r: _f(r.snow_depth_cm),
            reverse=True,
        ),
    }

    latest_date = max(weather)
    latest = {"weather": weather_brief(weather[latest_date]), "start": _start_payload(starts.get(latest_date))}
    years_ago: list[dict[str, Any]] = []
    for years in (1, 2, 3):
        target = latest_date - timedelta(days=364 * years)
        row = weather.get(target)
        if row is None:
            continue
        years_ago.append({"years": years, "weather": weather_brief(row), "start": _start_payload(starts.get(target))})

    return {
        "slug": identity.slug,
        "name": identity.name,
        "has_data": True,
        "months": months,
        "records": records,
        "latest": latest,
        "years_ago": years_ago,
        "attendance": _attendance_buckets(start_rows, starts),
        "starts_with_weather": len(start_rows),
    }


def build_location_weather(
    db: Session, slug: str, *, use_cache: bool = True, refresh: bool = False
) -> dict[str, Any] | None:
    key = location_weather_cache_key(slug)
    if use_cache and not refresh:
        try:
            raw = get_redis_client().get(key)
        except redis.RedisError:
            raw = None
        if isinstance(raw, str):
            try:
                return cast(dict[str, Any], json.loads(raw))
            except (TypeError, ValueError):
                pass
    payload = _compute_location_weather(db, slug)
    if payload is not None and use_cache:
        try:
            get_redis_client().setex(key, LOCATION_WEATHER_CACHE_TTL_SECONDS, json.dumps(payload, default=str))
        except redis.RedisError:
            pass
    return payload


def invalidate_location_weather_cache(slug: str) -> None:
    try:
        get_redis_client().delete(location_weather_cache_key(slug))
    except redis.RedisError:
        pass


# --------------------------------------------------------------------------- неделя


def week_weather_extremes(db: Session, saturday: date) -> dict[str, Any] | None:
    """Крайности субботы по всей стране: самый холодный, тёплый, мокрый, ветреный старт."""

    from app.services.location_page_service import _dedupe_crosslinked_events

    rows = (
        db.query(StartWeather, Event.id, Location, Platform.code)
        .join(Event, (Event.location_id == StartWeather.location_id) & (Event.event_date == StartWeather.obs_date))
        .join(Location, Location.id == Event.location_id)
        .join(Platform, Platform.id == Event.platform_id)
        .filter(StartWeather.obs_date == saturday, Event.is_test_event.is_(False))
        .all()
    )
    if not rows:
        return None
    kept = _dedupe_crosslinked_events(db, [row[1] for row in rows])
    catalog_index = LocationCatalogIndex(db)
    seen: set[str] = set()
    items: list[tuple[StartWeather, Location, str]] = []
    for weather, event_id, location, code in rows:
        if event_id not in kept:
            continue
        identity = catalog_index.canonical_identity_key(location, code)
        if identity in seen:
            continue
        seen.add(identity)
        items.append((weather, location, code))
    if not items:
        return None

    def payload(item: tuple[StartWeather, Location, str]) -> dict[str, Any]:
        weather, location, code = item
        return {
            "location_name": catalog_index.display_name(location, code),
            "location_slug": location.external_key.strip().lower(),
            "platform_code": code,
            "weather": weather_brief(weather),
        }

    with_temp = [item for item in items if item[0].temperature_c is not None]
    wet = [item for item in items if is_rain(_f(item[0].precipitation_run_mm))]
    windy = [item for item in items if item[0].wind_gusts_ms is not None and float(item[0].wind_gusts_ms) >= 10]
    temps = [float(item[0].temperature_c) for item in with_temp]
    return {
        "locations_with_weather": len(items),
        "temperature_median_c": _median(temps),
        "rain_locations": len(wet),
        "coldest": payload(min(with_temp, key=lambda i: float(i[0].temperature_c))) if with_temp else None,
        "warmest": payload(max(with_temp, key=lambda i: float(i[0].temperature_c))) if with_temp else None,
        "wettest": payload(max(wet, key=lambda i: float(i[0].precipitation_run_mm))) if wet else None,
        "windiest": payload(max(windy, key=lambda i: float(i[0].wind_gusts_ms))) if windy else None,
        "is_preliminary": any(item[0].source == SOURCE_FORECAST for item in items),
    }


# --------------------------------------------------------------------------- бегун


@dataclass(frozen=True)
class RunWeatherRow:
    event_date: date
    platform_code: str
    location_name: str
    location_slug: str
    finish_time_sec: int | None
    weather: StartWeather


def user_run_weather_rows(db: Session, user_id: UUID) -> list[RunWeatherRow]:
    """Пробежки пользователя, у которых есть погода (зачётные, без вторичных кросслинков)."""

    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    query = (
        db.query(RunResult, Event, Location, Platform.code, StartWeather)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .join(
            StartWeather,
            (StartWeather.location_id == Event.location_id) & (StartWeather.obs_date == Event.event_date),
        )
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
        )
    )
    secondary = user_secondary_crosslinked_run_ids(db, user_id)
    if secondary:
        query = query.filter(RunResult.id.notin_(secondary))
    catalog_index = LocationCatalogIndex(db)
    rows: list[RunWeatherRow] = []
    for run, event, location, code, weather in query.all():
        rows.append(
            RunWeatherRow(
                event_date=event.event_date,
                platform_code=code,
                location_name=catalog_index.display_name(location, code),
                location_slug=location.external_key.strip().lower(),
                finish_time_sec=run.finish_time_sec,
                weather=weather,
            )
        )
    rows.sort(key=lambda row: row.event_date)
    return rows


def _run_item(row: RunWeatherRow) -> dict[str, Any]:
    return {
        "event_date": row.event_date.isoformat(),
        "platform_code": row.platform_code,
        "location_name": row.location_name,
        "location_slug": row.location_slug,
        "weather": weather_brief(row.weather),
    }


def user_weather_stats(db: Session, user_id: UUID) -> dict[str, Any]:
    """Личные крайности и счётчики по погоде — блок «Погода» на обзоре."""

    rows = user_run_weather_rows(db, user_id)
    if not rows:
        return {
            "runs_with_weather": 0,
            "coldest": None,
            "hottest": None,
            "wettest": None,
            "windiest": None,
            "snowiest": None,
            "rain_runs": 0,
            "frost_runs": 0,
            "heat_runs": 0,
            "snow_runs": 0,
        }
    with_temp = [row for row in rows if row.weather.temperature_c is not None]
    wet = [row for row in rows if is_rain(_f(row.weather.precipitation_run_mm))]
    windy = [row for row in rows if row.weather.wind_gusts_ms is not None]
    snowy = [row for row in rows if is_snow_cover(_f(row.weather.snow_depth_cm))]
    return {
        "runs_with_weather": len(rows),
        "coldest": _run_item(min(with_temp, key=lambda r: float(r.weather.temperature_c))) if with_temp else None,
        "hottest": _run_item(max(with_temp, key=lambda r: float(r.weather.temperature_c))) if with_temp else None,
        "wettest": _run_item(max(wet, key=lambda r: float(r.weather.precipitation_run_mm))) if wet else None,
        "windiest": _run_item(max(windy, key=lambda r: float(r.weather.wind_gusts_ms))) if windy else None,
        "snowiest": _run_item(max(snowy, key=lambda r: float(r.weather.snow_depth_cm))) if snowy else None,
        "rain_runs": len(wet),
        "frost_runs": sum(1 for r in with_temp if float(r.weather.temperature_c) <= FROST_C),
        "heat_runs": sum(1 for r in with_temp if float(r.weather.temperature_c) >= HEAT_C),
        "snow_runs": len(snowy),
    }


def group_by_year(rows: Iterable[RunWeatherRow]) -> dict[int, list[RunWeatherRow]]:
    grouped: dict[int, list[RunWeatherRow]] = defaultdict(list)
    for row in rows:
        grouped[row.event_date.year].append(row)
    return dict(grouped)
