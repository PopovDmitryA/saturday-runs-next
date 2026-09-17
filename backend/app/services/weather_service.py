"""Погода на стартах: архив Open-Meteo по координатам локации и датам.

Источник один — Open-Meteo Historical API (реанализ ERA5 / ERA5-Land по сетке
~10 км, часовые данные с 1940 г., бесплатно для некоммерческих проектов,
атрибуция CC BY 4.0). Сверка со станцией аэропорта Якутска 04.01.2025 09:00:
API −47…−48°, станция −49.0° — сетке можно верить даже в сибирский мороз.
Дождь сетка размазывает: Калуга 02.09.2023 (ливень весь старт) дала 0.3 мм
за 8–11 против ~2 мм на станции, Брянск и Ульяновск сошлись с памятью.

Что собираем (решения Дмитрия 06–07.09.2026):
* локации России любой системы + зарубежные RunPark и S95; зарубежный parkrun
  пока не трогаем;
* все субботы с первого старта локации плюс даты внесубботних стартов
  (1 января и т.п.) — суббота без старта в −45 тоже часть картины; у закрытых
  площадок субботы кончаются через INACTIVE_TAIL_DAYS после последнего старта;
* час старта — по расписанию локации (schedule_parsed описания 5 вёрст),
  иначе 09:00 местного (S95 всегда 9:00, у parkrun РФ расписания нет);
* окна осадков вокруг старта: «бежали под дождём» старт−1ч…старт+2ч,
  «трасса мокрая» старт−4ч…старт−1ч.

Лимиты API считаются по весу: 1 вызов = 1 локация × 2 недели × 10 переменных,
год с 23 переменными стоил ~60, и 10 000 в сутки кончались на 30 локациях.
Поэтому просим ровно 10 часовых переменных, а суточные минимум/максимум/суммы
и восход-закат считаем сами: год = 26 условных вызовов.
"""

from __future__ import annotations

import logging
import math
import time as _time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Event, Location, LocationDescription, Platform, StartWeather
from app.services.location_schedule_service import start_time_for_date

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# Прогнозная модель отдаёт и прошедшие дни (до 92 суток): из неё берём
# субботу в саму субботу, пока архив не догнал.
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# Лестница источников: чем ниже строка, тем окончательнее.
#   forecast — прогнозная модель, снимок субботним вечером;
#   archive  — best_match, склейка ERA5 с оперативной моделью (доступна сразу);
#   era5     — чистый реанализ, догоняет с отставанием ~5 суток и на этом
#              останавливается: дальше строка не меняется.
# Замер 17.09.2026 на 30 локациях (станция ближе 6 км, 4086 сверок): средняя
# ошибка era5 0.98° против 1.20° у best_match, и era5 точнее на 28 локациях
# из 30. По осадкам разницы нет — их сетка размазывает у обеих моделей.
FINAL_MODEL = "era5"
MODEL_FORECAST_NAME = "best_match"
FALLBACK_MODEL = "best_match"
SOURCE_ERA5 = "era5"
SOURCE_ARCHIVE = "archive"
SOURCE_FORECAST = "forecast"
DEFAULT_START_TIME = time(9, 0)
# Оперативная склейка (best_match) доступна почти сразу: 2 суток хватает.
ARCHIVE_LAG_DAYS = 2
# Чистый ERA5 отстаёт на 5 суток (проверено 17.09.2026: при «сегодня» 16.09
# последние данные за 11.09). Пока он не догнал, держим строку на best_match и
# перезаписываем её следующим прогоном.
ERA5_LAG_DAYS = 6
# Площадка без стартов дольше этого срока считается закрытой: субботы после
# последнего старта + хвост не собираем.
INACTIVE_TAIL_DAYS = 45
FOREIGN_PLATFORMS_IN_SCOPE = ("runpark", "s95")

# Ровно 10 переменных — граница веса вызова.
HOURLY_VARS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "snowfall",
    "snow_depth",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
)


@dataclass(frozen=True)
class WeatherLocation:
    id: UUID
    name: str
    platform_code: str
    country: str | None
    latitude: float
    longitude: float
    schedule: list[dict[str, object]] | None


@dataclass
class CollectStats:
    dates_requested: int = 0
    dates_already: int = 0
    rows_written: int = 0
    api_calls: int = 0
    skipped_no_data: int = 0


class RateLimitDaily(RuntimeError):
    """Суточный лимит Open-Meteo исчерпан — продолжать сегодня бессмысленно."""


# --------------------------------------------------------------------------- scope


def list_scope_locations(db: Session, name_filters: Sequence[str] | None = None) -> list[WeatherLocation]:
    """Локации в периметре сбора: РФ любой системы + зарубежные RunPark/S95, с координатами и стартами."""

    stmt = (
        select(
            Location.id,
            Location.name,
            Platform.code,
            Location.country,
            Location.latitude,
            Location.longitude,
            LocationDescription.schedule_parsed,
        )
        .join(Platform, Platform.id == Location.platform_id)
        .outerjoin(LocationDescription, LocationDescription.location_id == Location.id)
        .where(Location.latitude.isnot(None), Location.longitude.isnot(None))
        .where((Location.country == "Россия") | Platform.code.in_(FOREIGN_PLATFORMS_IN_SCOPE))
        .where(select(Event.id).where(Event.location_id == Location.id, Event.is_test_event.is_(False)).exists())
        .order_by(Platform.code, Location.name)
    )
    rows = db.execute(stmt).all()
    result: list[WeatherLocation] = []
    for row in rows:
        loc_id, name, code, country, lat, lon, schedule = row
        if name_filters and not any(f.lower() in name.lower() for f in name_filters):
            continue
        result.append(
            WeatherLocation(
                id=loc_id,
                name=name,
                platform_code=code,
                country=country,
                latitude=float(lat),
                longitude=float(lon),
                schedule=schedule or None,
            )
        )
    return result


def event_dates_for_location(db: Session, location_id: UUID) -> list[date]:
    stmt = (
        select(Event.event_date)
        .where(Event.location_id == location_id, Event.is_test_event.is_(False))
        .distinct()
        .order_by(Event.event_date)
    )
    return [row[0] for row in db.execute(stmt).all()]


def stored_dates_for_location(db: Session, location_id: UUID) -> dict[date, str]:
    """Даты, уже лежащие в таблице, → источник строки (archive / forecast)."""

    stmt = select(StartWeather.obs_date, StartWeather.source).where(StartWeather.location_id == location_id)
    return {row[0]: row[1] for row in db.execute(stmt).all()}


def dates_to_fetch(dates: Sequence[date], stored: dict[date, str], *, preliminary: bool) -> list[date]:
    """Что просить у API при докачке.

    Архивный прогон берёт всё, что ещё не окончательно: пустые даты,
    предварительные строки и строки best_match, до которых ERA5 уже дошёл.
    Предварительный прогон — только даты, которых нет вовсе.
    """

    if preliminary:
        return [d for d in dates if d not in stored]
    return [d for d in dates if stored.get(d) != SOURCE_ERA5]


# --------------------------------------------------------------------------- dates & time


def saturdays_between(start: date, end: date) -> list[date]:
    if start > end:
        return []
    first = start + timedelta(days=(5 - start.weekday()) % 7)
    result: list[date] = []
    current = first
    while current <= end:
        result.append(current)
        current += timedelta(days=7)
    return result


def observation_dates(
    event_dates: Iterable[date],
    *,
    today: date,
    since: date | None = None,
    events_only: bool = False,
    upper: date | None = None,
) -> list[date]:
    """Все субботы с первого старта по границу архива + внесубботние даты стартов.

    upper — верхняя граница вместо «сегодня минус лаг архива» (предварительный
    прогон просит до сегодняшнего дня включительно). У закрытой площадки
    (последний старт раньше, чем INACTIVE_TAIL_DAYS назад) субботы кончаются
    через хвост после последнего старта.
    """

    events = sorted(set(event_dates))
    if not events:
        return []
    if upper is None:
        upper = today - timedelta(days=ARCHIVE_LAG_DAYS)
    tail_end = events[-1] + timedelta(days=INACTIVE_TAIL_DAYS)
    if tail_end < upper:
        upper = tail_end
    lower = events[0]
    if since and since > lower:
        lower = since
    wanted = {d for d in events if lower <= d <= upper}
    if not events_only:
        wanted.update(saturdays_between(lower, upper))
    return sorted(wanted)


def resolve_start_time(schedule: list[dict[str, object]] | None, on_date: date) -> tuple[time, str]:
    """Время старта на дату: расписание локации, иначе 09:00 местного."""

    scheduled = start_time_for_date(schedule, on_date)
    if scheduled is not None:
        return scheduled, "schedule"
    return DEFAULT_START_TIME, "default"


def sample_hour(start: time) -> int:
    """Ближайший к старту целый час: 08:30 → 9, 09:15 → 9."""

    hour = start.hour + (1 if start.minute >= 30 else 0)
    return min(hour, 23)


def sun_times(latitude: float, longitude: float, on_date: date, tz: ZoneInfo) -> tuple[time | None, time | None]:
    """Восход и закат по местному времени (уравнение восхода NOAA, точность ~1–2 мин).

    Полярный день/ночь → (None, None).
    """

    jd_noon = on_date.toordinal() + 1721424.5 + 0.5
    n = jd_noon - 2451545.0 + 0.0008
    j_star = n - longitude / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360.0)
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180.0 + 102.9372) % 360.0)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sin_decl = math.sin(lam) * math.sin(math.radians(23.4397))
    decl = math.asin(sin_decl)
    phi = math.radians(latitude)
    cos_omega = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_decl) / (math.cos(phi) * math.cos(decl))
    if cos_omega <= -1.0 or cos_omega >= 1.0:
        return None, None
    omega = math.degrees(math.acos(cos_omega))

    def to_local(jd: float) -> time:
        unix = (jd - 2440587.5) * 86400.0
        return datetime.fromtimestamp(unix, tz=UTC).astimezone(tz).time().replace(second=0, microsecond=0)

    return to_local(j_transit - omega / 360.0), to_local(j_transit + omega / 360.0)


# --------------------------------------------------------------------------- API


def fetch_archive(
    client: httpx.Client,
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    *,
    retries: int = 5,
    wait_hourly_reset: bool = True,
    endpoint: str = OPEN_METEO_ARCHIVE_URL,
    model: str = FINAL_MODEL,
    extra_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Один вызов архива (или прогнозной модели, endpoint=OPEN_METEO_FORECAST_URL).

    wait_hourly_reset=False — часовой лимит считать концом прогона (для
    beat-задачи: не держать воркер час в ожидании)."""

    params = {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "auto",
        "wind_speed_unit": "ms",
        "models": model,
    }
    if extra_params:
        params.update(extra_params)
    delay = 2.0
    hourly_waits = 0
    attempt = 0
    while True:
        try:
            response = client.get(endpoint, params=params, timeout=60)
        except httpx.TransportError as exc:
            # Случайный обрыв сети (таймаут TLS-рукопожатия 08.09.2026 на проде):
            # повторяем с нарастающей паузой, как и 5xx.
            attempt += 1
            if attempt >= retries:
                raise RuntimeError(f"Open-Meteo network error after {retries} attempts: {exc}") from exc
            _time.sleep(delay)
            delay *= 2
            continue
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            reason = response.text.lower()
            if "daily" in reason:
                raise RateLimitDaily(response.text[:200])
            if "hourly" in reason:
                # Часовой лимит: ждём до следующего часа и пробуем снова (не
                # больше двух раз — иначе это уже суточный лимит под другим именем).
                hourly_waits += 1
                if not wait_hourly_reset or hourly_waits > 2:
                    raise RateLimitDaily(response.text[:200])
                _time.sleep(_seconds_to_next_hour() + 30)
                continue
            # Минутный лимит считается по весу — ждём полную минуту.
            _time.sleep(61)
            continue
        attempt += 1
        if response.status_code in (500, 502, 503, 504) and attempt < retries:
            _time.sleep(delay)
            delay *= 2
            continue
        raise RuntimeError(f"Open-Meteo {response.status_code}: {response.text[:300]}")


def _seconds_to_next_hour() -> float:
    now = datetime.now(UTC)
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


def _dec(value: Any, scale: int) -> Decimal | None:
    if value is None:
        return None
    return round(Decimal(str(value)), scale)


def _int(value: Any) -> int | None:
    if value is None:
        return None
    return int(round(float(value)))


def window_sum(values: list[Any] | None, hour_index: dict[str, int], obs_date: date, hours: range) -> Decimal | None:
    """Сумма часовых значений за окно. Значение с меткой HH:00 у Open-Meteo — за час (HH−1, HH].

    Окно «с 8 до 11» = метки 09, 10, 11. Если какого-то часа нет в ответе — None.
    """

    if values is None:
        return None
    total = Decimal("0")
    for hour in hours:
        i = hour_index.get(f"{obs_date.isoformat()}T{hour:02d}:00")
        if i is None or i >= len(values) or values[i] is None:
            return None
        total += Decimal(str(values[i]))
    return round(total, 2)


def _day_values(values: list[Any] | None, hour_index: dict[str, int], obs_date: date) -> list[float]:
    if values is None:
        return []
    result: list[float] = []
    for hour in range(24):
        i = hour_index.get(f"{obs_date.isoformat()}T{hour:02d}:00")
        if i is not None and i < len(values) and values[i] is not None:
            result.append(float(values[i]))
    return result


def build_rows(
    payload: dict[str, Any],
    location: WeatherLocation,
    dates: Iterable[date],
    *,
    fetched_at: datetime,
    source: str = SOURCE_ARCHIVE,
) -> tuple[list[dict[str, Any]], int]:
    """Ответ API → строки start_weather. Второе значение — даты без данных (архив ещё не догнал)."""

    hourly = payload.get("hourly") or {}
    hour_index = {stamp: i for i, stamp in enumerate(hourly.get("time") or [])}
    tz = ZoneInfo(payload.get("timezone") or "UTC")

    def h(var: str, i: int) -> Any:
        values = hourly.get(var)
        return values[i] if values is not None and i < len(values) else None

    rows: list[dict[str, Any]] = []
    skipped = 0
    for obs_date in dates:
        start_time, _source = resolve_start_time(location.schedule, obs_date)
        start_hour = sample_hour(start_time)
        hi = hour_index.get(f"{obs_date.isoformat()}T{start_hour:02d}:00")
        if hi is None or h("temperature_2m", hi) is None:
            skipped += 1
            continue
        snow_depth_m = h("snow_depth", hi)
        precipitation = hourly.get("precipitation")
        day_temp = _day_values(hourly.get("temperature_2m"), hour_index, obs_date)
        day_precip = _day_values(precipitation, hour_index, obs_date)
        day_snow = _day_values(hourly.get("snowfall"), hour_index, obs_date)
        day_wind = _day_values(hourly.get("wind_speed_10m"), hour_index, obs_date)
        day_gusts = _day_values(hourly.get("wind_gusts_10m"), hour_index, obs_date)
        day_codes = _day_values(hourly.get("weather_code"), hour_index, obs_date)
        sunrise, sunset = sun_times(location.latitude, location.longitude, obs_date, tz)
        rows.append(
            {
                "location_id": location.id,
                "obs_date": obs_date,
                "start_time_local": start_time,
                "source": source,
                "temperature_c": _dec(h("temperature_2m", hi), 1),
                "apparent_temperature_c": _dec(h("apparent_temperature", hi), 1),
                "humidity_pct": _int(h("relative_humidity_2m", hi)),
                "precipitation_mm": _dec(h("precipitation", hi), 2),
                # Дождь между старт−1ч и старт+2ч: метки start..start+2.
                "precipitation_run_mm": window_sum(
                    precipitation, hour_index, obs_date, range(start_hour, start_hour + 3)
                ),
                # Дождь между старт−4ч и старт−1ч: метки start−3..start−1.
                "precipitation_before_mm": window_sum(
                    precipitation, hour_index, obs_date, range(max(start_hour - 3, 0), start_hour)
                ),
                "snowfall_cm": _dec(h("snowfall", hi), 2),
                "snow_depth_cm": _dec(snow_depth_m * 100, 1) if snow_depth_m is not None else None,
                "weather_code": _int(h("weather_code", hi)),
                "cloud_cover_pct": _int(h("cloud_cover", hi)),
                "wind_speed_ms": _dec(h("wind_speed_10m", hi), 1),
                "wind_gusts_ms": _dec(h("wind_gusts_10m", hi), 1),
                "day_temperature_min_c": _dec(min(day_temp), 1) if day_temp else None,
                "day_temperature_max_c": _dec(max(day_temp), 1) if day_temp else None,
                "day_precipitation_mm": _dec(sum(day_precip), 2) if day_precip else None,
                "day_snowfall_cm": _dec(sum(day_snow), 2) if day_snow else None,
                "day_precipitation_hours": _dec(sum(1 for v in day_precip if v > 0), 1) if day_precip else None,
                "day_wind_speed_max_ms": _dec(max(day_wind), 1) if day_wind else None,
                "day_wind_gusts_max_ms": _dec(max(day_gusts), 1) if day_gusts else None,
                # Самое суровое явление суток — как daily weather_code у Open-Meteo.
                "day_weather_code": _int(max(day_codes)) if day_codes else None,
                "sunrise_local": sunrise,
                "sunset_local": sunset,
                "fetched_at": fetched_at,
            }
        )
    return rows, skipped


def upsert_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = insert(StartWeather).values(rows)
    key = {"location_id", "obs_date"}
    update_cols = {c.name: getattr(stmt.excluded, c.name) for c in StartWeather.__table__.columns if c.name not in key}
    stmt = stmt.on_conflict_do_update(index_elements=["location_id", "obs_date"], set_=update_cols)
    db.execute(stmt)
    return len(rows)


# --------------------------------------------------------------------------- orchestration


def remember_timezone(db: Session, location_id: UUID, timezone_name: str | None) -> None:
    """Имя зоны из ответа API → locations.timezone (свойство локации, не строки погоды)."""

    if not timezone_name:
        return
    location = db.get(Location, location_id)
    if location is not None and location.timezone != timezone_name:
        location.timezone = timezone_name


# Вес вызова считается по длине периода: две недели = один вызов. Просить год
# ради одной свежей субботы — впустую жечь суточный лимит, поэтому режем не
# только по годам, но и по разрывам: подряд идущие даты собираются в диапазон,
# а одинокая суббота стоит своим коротким запросом.
CHUNK_GAP_DAYS = 45


def _year_chunks(dates: Sequence[date]) -> list[tuple[date, date, list[date]]]:
    chunks: list[list[date]] = []
    for day in sorted(dates):
        if chunks and day.year == chunks[-1][-1].year and (day - chunks[-1][-1]).days <= CHUNK_GAP_DAYS:
            chunks[-1].append(day)
        else:
            chunks.append([day])
    return [(group[0], group[-1], group) for group in chunks]


def collect_location_weather(
    db: Session,
    client: httpx.Client,
    location: WeatherLocation,
    *,
    today: date | None = None,
    since: date | None = None,
    events_only: bool = False,
    resume: bool = True,
    pause_seconds: float = 0.3,
    wait_hourly_reset: bool = True,
    preliminary: bool = False,
) -> CollectStats:
    """Собрать погоду одной локации: по вызову API на календарный год, upsert построчно.

    resume — не трогать даты, которые уже лежат в таблице (докачка после
    остановки по лимиту); год просится целиком, если в нём есть хоть одна
    недостающая дата. Архивный прогон заодно заменяет предварительные строки.

    preliminary — предварительная погода из прогнозной модели за дни, до
    которых архив ещё не дошёл (сегодня и лаг архива); строки помечаются
    source='forecast'.
    """

    stats = CollectStats()
    # Дата — по часам машины, не UTC: вечером в Москве 07.09 по UTC ещё 06.09,
    # и субботу 05.09 при лаге в 2 дня скрипт бы пропустил.
    today = today or date.today()
    archive_upper = today - timedelta(days=ARCHIVE_LAG_DAYS)
    event_dates = event_dates_for_location(db, location.id)
    if preliminary:
        dates = [d for d in observation_dates(event_dates, today=today, since=since, upper=today) if d > archive_upper]
    else:
        dates = observation_dates(event_dates, today=today, since=since, events_only=events_only)
    stats.dates_requested = len(dates)
    if resume or preliminary:
        stored = stored_dates_for_location(db, location.id)
        missing = dates_to_fetch(dates, stored, preliminary=preliminary)
        stats.dates_already = len(dates) - len(missing)
        dates = missing
    # Закрыть транзакцию чтения до похода в сеть: ожидание лимита длится до часа,
    # а Postgres рвёт idle-in-transaction через 60 секунд.
    db.rollback()
    endpoint = OPEN_METEO_FORECAST_URL if preliminary else OPEN_METEO_ARCHIVE_URL
    era5_upper = today - timedelta(days=ERA5_LAG_DAYS)
    for _start, _end, chunk_dates in _year_chunks(dates):
        # Даты, до которых ERA5 дошёл, берём у него — это окончательная строка;
        # свежие добираем best_match и помечаем как промежуточные.
        groups: list[tuple[str, str, list[date]]] = []
        if preliminary:
            groups.append((MODEL_FORECAST_NAME, SOURCE_FORECAST, chunk_dates))
        else:
            final_dates = [d for d in chunk_dates if d <= era5_upper]
            fresh_dates = [d for d in chunk_dates if d > era5_upper]
            if final_dates:
                groups.append((FINAL_MODEL, SOURCE_ERA5, final_dates))
            if fresh_dates:
                groups.append((FALLBACK_MODEL, SOURCE_ARCHIVE, fresh_dates))
        for model, source, group_dates in groups:
            payload = fetch_archive(
                client,
                location.latitude,
                location.longitude,
                group_dates[0],
                group_dates[-1],
                wait_hourly_reset=wait_hourly_reset,
                endpoint=endpoint,
                model=model,
            )
            stats.api_calls += 1
            remember_timezone(db, location.id, payload.get("timezone"))
            rows, skipped = build_rows(payload, location, group_dates, fetched_at=datetime.now(UTC), source=source)
            stats.skipped_no_data += skipped
            stats.rows_written += upsert_rows(db, rows)
            db.commit()
            if pause_seconds:
                _time.sleep(pause_seconds)
    return stats


def coverage_summary(db: Session) -> tuple[int, int]:
    """(строк, локаций) в start_weather — для сводки в конце прогона."""

    row = db.execute(select(func.count(), func.count(func.distinct(StartWeather.location_id)))).one()
    return int(row[0]), int(row[1])


@dataclass
class ScopeRunSummary:
    scope_locations: int = 0
    locations_complete: int = 0
    locations_touched: int = 0
    rows_written: int = 0
    api_calls: int = 0
    skipped_no_data: int = 0
    rows_total: int = 0
    locations_total: int = 0
    stopped_by_limit: bool = False
    limit_reason: str = ""
    error: str = ""
    preliminary: bool = False

    @property
    def finished(self) -> bool:
        """Все локации периметра собраны до границы архива и прогон не прерван."""

        return not self.stopped_by_limit and not self.error and self.locations_complete == self.scope_locations


def collect_scope(
    db: Session,
    client: httpx.Client,
    *,
    name_filters: Sequence[str] | None = None,
    since: date | None = None,
    events_only: bool = False,
    resume: bool = True,
    pause_seconds: float = 0.5,
    wait_hourly_reset: bool = True,
    preliminary: bool = False,
    on_location: Any = None,
) -> ScopeRunSummary:
    """Прогон по всему периметру с остановкой на суточном лимите.

    on_location(location, stats) — колбэк для построчного лога скрипта.
    """

    summary = ScopeRunSummary(preliminary=preliminary)
    locations = list_scope_locations(db, name_filters)
    summary.scope_locations = len(locations)
    for location in locations:
        try:
            stats = collect_location_weather(
                db,
                client,
                location,
                since=since,
                events_only=events_only,
                resume=resume,
                pause_seconds=pause_seconds,
                wait_hourly_reset=wait_hourly_reset,
                preliminary=preliminary,
            )
        except RateLimitDaily as exc:
            db.rollback()
            summary.stopped_by_limit = True
            summary.limit_reason = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 — сводка с уже собранным важнее трейсбека
            # Ошибка посреди прогона не должна обнулять отчёт: собранное по
            # предыдущим локациям уже закоммичено, его и показываем.
            logger.exception("Сбор погоды: локация %s", location.name)
            db.rollback()
            summary.error = f"{location.name}: {type(exc).__name__}: {exc}"[:300]
            break
        summary.rows_written += stats.rows_written
        summary.api_calls += stats.api_calls
        summary.skipped_no_data += stats.skipped_no_data
        if stats.api_calls:
            summary.locations_touched += 1
        # Локация закрыта, если после прогона не осталось дат без данных.
        if stats.dates_already + stats.rows_written >= stats.dates_requested:
            summary.locations_complete += 1
        if on_location is not None:
            on_location(location, stats)
    summary.rows_total, summary.locations_total = coverage_summary(db)
    return summary


def format_run_report(summary: ScopeRunSummary, *, when: datetime, backfill_reported: bool = False) -> str:
    """Текст отчёта в Telegram: за прогон, всего, и что дальше.

    backfill_reported — «сбор завершён» уже объявляли: дальше это еженедельная
    докачка суббот, а не финиш бэкфила.
    """

    if summary.preliminary:
        lines = [f"🌦 Погода на стартах — предварительно, {when.strftime('%d.%m.%Y %H:%M')}"]
        lines.append(
            f"Из прогнозной модели: {summary.rows_written} строк по {summary.locations_touched} локациям, "
            f"вызовов Open-Meteo {summary.api_calls}; архив заменит их в понедельник"
        )
        if summary.error:
            lines.append(f"⚠️ Прогон прерван ошибкой: {summary.error}")
        return "\n".join(lines)
    lines = [f"🌦 Погода на стартах — сбор {when.strftime('%d.%m.%Y %H:%M')}"]
    lines.append(
        f"За прогон: записано {summary.rows_written} строк по {summary.locations_touched} локациям, "
        f"вызовов Open-Meteo {summary.api_calls}"
    )
    lines.append(
        f"Всего в таблице: {summary.rows_total} строк, {summary.locations_total} локаций; "
        f"собрано полностью {summary.locations_complete} из {summary.scope_locations}"
    )
    if summary.error:
        lines.append(f"⚠️ Прогон прерван ошибкой: {summary.error}")
    elif summary.stopped_by_limit:
        lines.append("Остановлено лимитом Open-Meteo, продолжу завтра")
    elif summary.finished and not backfill_reported:
        lines.append("✅ Сбор всего периметра завершён — можно обрабатывать данные")
        lines.append("Сессия Claude: «Погода на стартах» (ветка historical-weather-starts)")
    elif summary.finished:
        lines.append("Еженедельная докачка: все локации периметра закрыты до границы архива")
    return "\n".join(lines)
