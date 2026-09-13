"""Рейтинг «Погода» — ответ /api/weather-rating (см. weather_rating_service)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.weather import WeatherBriefResponse


class WalrusRowResponse(BaseModel):
    place: int
    name: str
    handle: str | None = None
    platform_code: str
    count: int
    coldest_c: float | None = None
    coldest_label: str = ""
    coldest_date: str | None = None
    coldest_location: str | None = None


class WalrusesResponse(BaseModel):
    by_count: list[WalrusRowResponse] = Field(default_factory=list)
    coldest: list[WalrusRowResponse] = Field(default_factory=list)
    participants: int = 0
    threshold_c: float = -20.0


class SeasonLocationRowResponse(BaseModel):
    place: int
    name: str
    slug: str | None = None
    platform_code: str
    region: str | None = None
    starts: int
    median_c: float
    min_c: float
    max_c: float


class SeasonLocationsResponse(BaseModel):
    coldest_winter: list[SeasonLocationRowResponse] = Field(default_factory=list)
    hottest_summer: list[SeasonLocationRowResponse] = Field(default_factory=list)


class TemperatureBucketResponse(BaseModel):
    from_c: int
    to_c: int
    label: str
    finishes: int
    starts: int
    avg_finish_sec: int | None = None
    avg_finishers: float | None = None


class WeatherExtremeResponse(BaseModel):
    location_name: str
    location_slug: str | None = None
    platform_code: str
    finishers: int | None = None
    event_number: int | None = None
    weather: WeatherBriefResponse


class WeatherRatingResponse(BaseModel):
    walruses: WalrusesResponse
    locations: SeasonLocationsResponse
    temperature: list[TemperatureBucketResponse] = Field(default_factory=list)
    extremes: dict[str, WeatherExtremeResponse | None] = Field(default_factory=dict)
