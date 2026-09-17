"""Прогноз погоды на ближайший старт локации и советы «во что одеться».

Архив (start_weather_service) отвечает на вопрос «какая тут погода бывает»,
этот модуль — на вопрос «что будет в субботу». Раз в сутки задача
weather.collect_forecast спрашивает Open-Meteo прогноз по каждой локации
периметра и кладёт строку на ближайшую субботу; на странице локации она
превращается в блок «Прогноз на старт» с человеческими советами.

Честность прогноза: за неделю он гадание, накануне почти факт. Поэтому в
строке хранится horizon_days (для отчётов и будущих срезов), а на витрине
стоит только время последнего обновления.
"""

from __future__ import annotations

import time as _time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import StartWeatherForecast
from app.services.start_weather_service import format_temperature, weather_code_icon, weather_code_label
from app.services.weather_service import (
    OPEN_METEO_FORECAST_URL,
    WeatherLocation,
    _dec,
    _int,
    fetch_archive,
    list_scope_locations,
    resolve_start_time,
    sample_hour,
)

# Прогноз просим от сегодня до ближайшей субботы включительно: один вызов на
# локацию (меньше двух недель — вес ровно единица).
FORECAST_VARS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
)
# Дальше двух недель Open-Meteo не смотрит, да и смысла нет.
MAX_HORIZON_DAYS = 14

# Пороги советов. Дождь у прогноза вероятностный: 60% и выше — «будет», от 30
# до 60 — «может накрапывать».
RAIN_PROBABILITY_SURE = 60
RAIN_PROBABILITY_MAYBE = 30
RAIN_MM = 1.0
WIND_STRONG_MS = 8.0
GUST_STRONG_MS = 12.0
FROST_C = -10.0
COLD_C = 0.0
# Между нулём и семью градусами на старте зябко: люди приезжают в футболке
# и мёрзнут в очереди, поэтому это отдельная ступень совета.
COOL_C = 7.0
HEAT_C = 25.0
ICE_RANGE = (-3.0, 2.0)


@dataclass
class ForecastStats:
    locations: int = 0
    rows_written: int = 0
    api_calls: int = 0
    skipped: int = 0


def next_start_date(today: date) -> date:
    """Ближайшая суббота, считая сегодняшнюю: в субботу утром прогноз ещё нужен."""

    return today + timedelta(days=(5 - today.weekday()) % 7)


# Воскресенье — единственный день, когда прогноз не собираем: до старта ещё
# шесть суток, на таком горизонте это гадание. Показ начинается в понедельник
# (решение Дмитрия 17.09.2026), а строки прошедшей субботы к этому моменту уже
# удалены — блока на витрине просто нет.
SUNDAY = 6


def forecast_day(today: date) -> bool:
    return today.weekday() != SUNDAY


def _window_hours(start: time) -> range:
    hour = sample_hour(start)
    return range(hour, hour + 3)


def build_forecast_row(
    payload: dict[str, Any],
    location: WeatherLocation,
    target: date,
    *,
    today: date,
    fetched_at: datetime,
) -> dict[str, Any] | None:
    """Ответ прогнозного API → строка start_weather_forecast (или None, если пусто)."""

    hourly = payload.get("hourly") or {}
    index = {stamp: i for i, stamp in enumerate(hourly.get("time") or [])}
    start_time, _source = resolve_start_time(location.schedule, target)
    hour = sample_hour(start_time)
    at = index.get(f"{target.isoformat()}T{hour:02d}:00")

    def value(name: str, position: int | None) -> Any:
        values = hourly.get(name)
        if position is None or values is None or position >= len(values):
            return None
        return values[position]

    if at is None or value("temperature_2m", at) is None:
        return None

    window = [index.get(f"{target.isoformat()}T{h:02d}:00") for h in _window_hours(start_time)]
    rain_mm = 0.0
    probability = 0
    for position in window:
        precipitation = value("precipitation", position)
        if precipitation is not None:
            rain_mm += float(precipitation)
        chance = value("precipitation_probability", position)
        if chance is not None:
            probability = max(probability, int(chance))

    return {
        "location_id": location.id,
        "target_date": target,
        "start_time_local": start_time,
        "temperature_c": _dec(value("temperature_2m", at), 1),
        "apparent_temperature_c": _dec(value("apparent_temperature", at), 1),
        "humidity_pct": _int(value("relative_humidity_2m", at)),
        "precipitation_mm": _dec(rain_mm, 2),
        "precipitation_probability_pct": probability,
        "snowfall_cm": _dec(value("snowfall", at), 2),
        "weather_code": _int(value("weather_code", at)),
        "cloud_cover_pct": _int(value("cloud_cover", at)),
        "wind_speed_ms": _dec(value("wind_speed_10m", at), 1),
        "wind_gusts_ms": _dec(value("wind_gusts_10m", at), 1),
        "horizon_days": (target - today).days,
        "fetched_at": fetched_at,
    }


def upsert_forecast_rows(db: Session, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statement = insert(StartWeatherForecast).values(list(rows))
    key = {"location_id", "target_date"}
    updates = {
        column.name: getattr(statement.excluded, column.name)
        for column in StartWeatherForecast.__table__.columns
        if column.name not in key
    }
    statement = statement.on_conflict_do_update(index_elements=["location_id", "target_date"], set_=updates)
    db.execute(statement)
    return len(rows)


def drop_past_forecasts(db: Session, today: date) -> int:
    """Прогноз на прошедшие субботы не нужен: факт уже в start_weather."""

    result = db.execute(delete(StartWeatherForecast).where(StartWeatherForecast.target_date < today))
    return int(result.rowcount or 0)


def collect_forecasts(
    db: Session,
    client: httpx.Client,
    *,
    today: date | None = None,
    name_filters: Sequence[str] | None = None,
    pause_seconds: float = 0.2,
    wait_hourly_reset: bool = False,
) -> ForecastStats:
    """Прогноз на ближайшую субботу по всем локациям периметра."""

    today = today or date.today()
    if not forecast_day(today):
        drop_past_forecasts(db, today)
        db.commit()
        return ForecastStats()
    target = next_start_date(today)
    stats = ForecastStats()
    locations = list_scope_locations(db, name_filters)
    stats.locations = len(locations)
    db.rollback()
    for location in locations:
        payload = fetch_archive(
            client,
            location.latitude,
            location.longitude,
            today,
            target,
            wait_hourly_reset=wait_hourly_reset,
            endpoint=OPEN_METEO_FORECAST_URL,
            model="best_match",
            extra_params={"hourly": ",".join(FORECAST_VARS)},
        )
        stats.api_calls += 1
        row = build_forecast_row(payload, location, target, today=today, fetched_at=datetime.now(UTC))
        if row is None:
            stats.skipped += 1
        else:
            stats.rows_written += upsert_forecast_rows(db, [row])
        db.commit()
        if pause_seconds:
            _time.sleep(pause_seconds)
    drop_past_forecasts(db, today)
    db.commit()
    return stats


# --------------------------------------------------------------------------- витрина


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


# Советы держим короткими: карточка стоит в шапке локации рядом с названием,
# и абзац текста там читать никто не будет (Дмитрий, 17.09.2026). Порядок
# важности — осадки, температура, ветер: компактный вид показывает первые два.
def forecast_advice(row: StartWeatherForecast) -> list[str]:
    """Короткие советы к прогнозу: во что одеться и чего ждать на трассе."""

    temp = _f(row.temperature_c)
    apparent = _f(row.apparent_temperature_c)
    rain_mm = _f(row.precipitation_mm) or 0.0
    probability = int(row.precipitation_probability_pct or 0)
    wind = _f(row.wind_speed_ms) or 0.0
    gusts = _f(row.wind_gusts_ms) or 0.0
    snow = _f(row.snowfall_cm) or 0.0
    advice: list[str] = []

    if snow > 0:
        advice.append("Снег — трасса скользкая")
    elif probability >= RAIN_PROBABILITY_SURE or rain_mm >= RAIN_MM:
        advice.append("Будет дождь — нужна ветровка")
    elif probability >= RAIN_PROBABILITY_MAYBE:
        advice.append("Дождь возможен")

    if temp is not None:
        if temp <= FROST_C:
            advice.append("Мороз: шапка и перчатки")
        elif temp <= COLD_C:
            advice.append("Ниже нуля — одевайтесь теплее")
        elif temp <= COOL_C:
            advice.append("Прохладно — длинный рукав")
        elif temp >= HEAT_C:
            advice.append("Жарко — возьмите воду")
        if ICE_RANGE[0] <= temp <= ICE_RANGE[1] and (rain_mm > 0 or probability >= RAIN_PROBABILITY_MAYBE):
            advice.append("Возможен гололёд")

    if gusts >= GUST_STRONG_MS or wind >= WIND_STRONG_MS:
        advice.append("Ветрено — нужен ветрозащитный слой")

    # «Ощущается» работает в обе стороны: ветер уводит вниз, влажная жара вверх.
    if temp is not None and apparent is not None and abs(temp - apparent) >= 5:
        advice.append(f"Ощущается как {format_temperature(apparent)}")

    if not advice:
        advice.append("Погода спокойная — обычной формы хватит")
    # Больше трёх строк — это уже не подсказка, а инструкция.
    return advice[:3]


def forecast_payload(row: StartWeatherForecast | None) -> dict[str, Any] | None:
    if row is None:
        return None
    code = int(row.weather_code) if row.weather_code is not None else None
    temp = _f(row.temperature_c)
    label = weather_code_label(code)
    parts = [f"{int(round(temp))}°" if temp is not None else "—"]
    if label:
        parts.append(label)
    wind = _f(row.wind_speed_ms)
    if wind is not None:
        parts.append("штиль" if wind < 1 else f"ветер {int(round(wind))} м/с")
    return {
        "target_date": row.target_date.isoformat(),
        "start_time_local": row.start_time_local.strftime("%H:%M") if row.start_time_local else None,
        "temperature_c": temp,
        "apparent_temperature_c": _f(row.apparent_temperature_c),
        "humidity_pct": row.humidity_pct,
        "precipitation_mm": _f(row.precipitation_mm),
        "precipitation_probability_pct": row.precipitation_probability_pct,
        "snowfall_cm": _f(row.snowfall_cm),
        "weather_code": code,
        "label": label,
        "icon": weather_code_icon(code),
        "cloud_cover_pct": row.cloud_cover_pct,
        "wind_speed_ms": wind,
        "wind_gusts_ms": _f(row.wind_gusts_ms),
        "horizon_days": row.horizon_days,
        "updated_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "summary": ", ".join(parts),
        "advice": forecast_advice(row),
    }


def forecast_for_locations(
    db: Session, location_ids: Iterable[UUID], *, today: date | None = None
) -> dict[str, Any] | None:
    """Прогноз идентичности локации: строка ближайшей субботы любой из её систем."""

    ids = list(location_ids)
    if not ids:
        return None
    today = today or date.today()
    row = (
        db.execute(
            select(StartWeatherForecast)
            .where(StartWeatherForecast.location_id.in_(ids), StartWeatherForecast.target_date >= today)
            .order_by(StartWeatherForecast.target_date.asc(), StartWeatherForecast.fetched_at.desc())
        )
        .scalars()
        .first()
    )
    return forecast_payload(row)
