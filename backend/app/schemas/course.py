"""Схемы паспорта трассы и рейтингов трасс."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CourseProfileResponse(BaseModel):
    """Паспорт трассы одной версии."""

    model_config = ConfigDict(from_attributes=True)

    course_version: int
    is_current: bool
    tracks_count: int
    unique_user_count: int
    distance_m: float | None = None
    distance_min_m: float | None = None
    distance_max_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_span_m: float | None = None
    turn_sum_deg: float | None = None
    u_turn_count: float | None = None
    longest_straight_m: float | None = None
    lap_count: int | None = None
    # Самый тесный прямоугольник вокруг трассы: короткая сторона, длинная
    # и площадь. «Пять километров умещаются в 150 × 300 метров».
    box_short_m: float | None = None
    box_long_m: float | None = None
    box_area_m2: float | None = None
    uphill_share: float | None = None
    downhill_share: float | None = None
    climb_length_m: float | None = None
    climb_rise_m: float | None = None
    climb_grade_percent: float | None = None
    # [[метры от старта, высота над низшей точкой], ...]
    elevation_profile: list[Any] = Field(default_factory=list)
    # Линия трассы для карты: [[широта, долгота], ...]
    geometry: list[Any] = Field(default_factory=list)
    first_track_at: datetime | None = None
    last_track_at: datetime | None = None


class LocationCourseResponse(BaseModel):
    location_slug: str
    location_name: str
    # Данных мало — на странице вместо цифр серая заглушка.
    has_data: bool = False
    current: CourseProfileResponse | None = None
    # Прошлые версии трассы: трассу меняли, эти цифры больше не актуальны.
    history: list[CourseProfileResponse] = Field(default_factory=list)


class CourseRatingItem(BaseModel):
    position: int | None = None
    location_slug: str
    location_name: str
    city: str | None = None
    region: str | None = None
    platform_code: str
    last_event_date: date | None = None
    has_data: bool = False
    value: float | None = None
    tracks_count: int = 0
    unique_user_count: int = 0
    distance_m: float | None = None
    elevation_span_m: float | None = None
    elevation_gain_m: float | None = None
    turn_sum_deg: float | None = None
    longest_straight_m: float | None = None
    lap_count: int | None = None
    # Самый тесный прямоугольник вокруг трассы: короткая сторона, длинная
    # и площадь. «Пять километров умещаются в 150 × 300 метров».
    box_short_m: float | None = None
    box_long_m: float | None = None
    box_area_m2: float | None = None


class CourseRatingResponse(BaseModel):
    metric: str
    items: list[CourseRatingItem] = Field(default_factory=list)
    # Сколько локаций уже с данными и сколько всего в рейтинге.
    with_data_count: int = 0
    total_count: int = 0
