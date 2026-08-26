from __future__ import annotations

from pydantic import BaseModel, Field


class LocationRecordRowResponse(BaseModel):
    """Строка рейтинга: площадка и её рекорд в выбранном зачёте."""

    place: int
    slug: str
    name: str
    city: str | None = None
    region: str | None = None
    is_paused: bool = False
    is_cancelled: bool = False
    finish_time_sec: int
    finish_time_display: str | None = None
    runner_name: str | None = None
    # Хендл профиля на сайте (slug или номер) — только у открытых профилей.
    runner_handle: str | None = None
    event_date: str | None = None
    # Система, в протоколе которой стоит рекорд (у площадки их может быть две).
    platform_code: str | None = None
    platform_label: str | None = None
    protocol_url: str | None = None


class LocationRecordsAgeGroupResponse(BaseModel):
    """Пункт селектора возрастных групп."""

    age_group: str
    key: str
    locations_count: int


class LocationRecordsRatingResponse(BaseModel):
    scope: str
    gender: str
    age_group: str | None = None
    platform: str = "all"
    rows: list[LocationRecordRowResponse] = Field(default_factory=list)
    age_groups: list[LocationRecordsAgeGroupResponse] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    # Ступень и пол зрителя по его последней пробежке на 5 вёрст: витрина
    # помечает ими пункт селектора, а рейтинг без явных фильтров открывается
    # сразу на них.
    viewer_age_group: str | None = None
    viewer_gender: str | None = None
