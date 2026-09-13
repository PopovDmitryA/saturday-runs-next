"""Погода на стартах — ответы API (см. start_weather_service)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WeatherBriefResponse(BaseModel):
    """Погода в час старта одной даты + готовая строка summary."""

    date: str
    start_time_local: str | None = None
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    humidity_pct: int | None = None
    weather_code: int | None = None
    label: str = ""
    icon: str = ""
    wind_speed_ms: float | None = None
    wind_gusts_ms: float | None = None
    # Осадки в окне «старт−1ч…старт+2ч» и «старт−4ч…старт−1ч» (трасса мокрая).
    precipitation_run_mm: float | None = None
    precipitation_before_mm: float | None = None
    snowfall_cm: float | None = None
    snow_depth_cm: float | None = None
    day_temperature_min_c: float | None = None
    day_temperature_max_c: float | None = None
    day_precipitation_mm: float | None = None
    sunrise_local: str | None = None
    # dry / drizzle / rain / downpour — см. пороги в start_weather_service.
    rain_kind: str = "dry"
    is_rain: bool = False
    is_snow_cover: bool = False
    # Предварительная строка из прогнозной модели (субботний вечер), архив заменит.
    is_preliminary: bool = False
    summary: str


class WeatherStartRefResponse(BaseModel):
    platform_code: str
    event_number: int | None = None
    finishers: int | None = None


class WeatherRecordResponse(BaseModel):
    weather: WeatherBriefResponse
    start: WeatherStartRefResponse | None = None


class LocationWeatherMonthResponse(BaseModel):
    month: int
    label: str
    # Сколько стартов площадки пришлось на этот месяц (с погодой).
    starts: int = 0
    # starts — статистика по стартам; saturdays — стартов в месяце не было, взяты все субботы; none — данных нет.
    basis: str = "none"
    samples: int = 0
    temperature_median_c: float | None = None
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    apparent_median_c: float | None = None
    rain_share: float | None = None
    snow_share: float | None = None


class LocationWeatherAttendanceResponse(BaseModel):
    key: str
    label: str
    starts: int
    avg_finishers: float | None = None


class LocationWeatherYearAgoResponse(BaseModel):
    years: int
    weather: WeatherBriefResponse
    start: WeatherStartRefResponse | None = None


class LocationWeatherResponse(BaseModel):
    slug: str
    name: str
    has_data: bool
    months: list[LocationWeatherMonthResponse] = Field(default_factory=list)
    records: dict[str, WeatherRecordResponse | None] = Field(default_factory=dict)
    latest: WeatherRecordResponse | None = None
    years_ago: list[LocationWeatherYearAgoResponse] = Field(default_factory=list)
    attendance: list[LocationWeatherAttendanceResponse] = Field(default_factory=list)
    starts_with_weather: int = 0


class WeekWeatherLocationResponse(BaseModel):
    location_name: str
    location_slug: str | None = None
    platform_code: str
    weather: WeatherBriefResponse


class WeekWeatherResponse(BaseModel):
    locations_with_weather: int = 0
    temperature_median_c: float | None = None
    rain_locations: int = 0
    coldest: WeekWeatherLocationResponse | None = None
    warmest: WeekWeatherLocationResponse | None = None
    wettest: WeekWeatherLocationResponse | None = None
    windiest: WeekWeatherLocationResponse | None = None
    is_preliminary: bool = False


class UserWeatherRunResponse(BaseModel):
    event_date: str
    platform_code: str
    location_name: str
    location_slug: str | None = None
    weather: WeatherBriefResponse


class UserWeatherStatsResponse(BaseModel):
    runs_with_weather: int = 0
    coldest: UserWeatherRunResponse | None = None
    hottest: UserWeatherRunResponse | None = None
    wettest: UserWeatherRunResponse | None = None
    windiest: UserWeatherRunResponse | None = None
    snowiest: UserWeatherRunResponse | None = None
    rain_runs: int = 0
    frost_runs: int = 0
    heat_runs: int = 0
    snow_runs: int = 0
