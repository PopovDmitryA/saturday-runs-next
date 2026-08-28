from __future__ import annotations

from pydantic import BaseModel, Field


class RegionRatingRowResponse(BaseModel):
    """Строка рейтинга: регион России или зарубежная страна."""

    place: int
    name: str
    # region | country — зарубежье считается по странам, как на карте.
    scope: str
    locations: int
    # Площадки со статусом «не действует»: в счётчик не входят, показываются
    # подписью.
    paused: int = 0
    cities: int = 0
    # Сколько площадок области есть в каждой системе. Сумма бывает больше
    # locations: одна площадка живёт сразу в двух системах.
    by_platform: dict[str, int] = Field(default_factory=dict)


class RegionRatingTotalsResponse(BaseModel):
    regions: int = 0
    region_locations: int = 0
    countries: int = 0
    country_locations: int = 0
    paused: int = 0
    unknown_region: int = 0


class RegionsRatingResponse(BaseModel):
    platform: str = "all"
    platforms: list[str] = Field(default_factory=list)
    regions: list[RegionRatingRowResponse] = Field(default_factory=list)
    countries: list[RegionRatingRowResponse] = Field(default_factory=list)
    totals: RegionRatingTotalsResponse = Field(default_factory=RegionRatingTotalsResponse)
