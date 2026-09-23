"""Схемы треков пробежек: что отдаём в кабинет."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TrackLinkRequest(BaseModel):
    """Импорт по ссылке на публичную активность Garmin Connect."""

    url: str = Field(min_length=10, max_length=1024)


class TrackSummaryResponse(BaseModel):
    """Трек без геометрии — для списков и значка в таблице пробежек."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_result_id: UUID | None = None
    location_id: UUID | None = None
    source: str
    source_url: str | None = None
    started_at: datetime | None = None
    duration_sec: int | None = None
    # Наш замер по сглаженной геометрии и то, что показал прибор.
    distance_m: float | None = None
    device_distance_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    min_elevation_m: float | None = None
    max_elevation_m: float | None = None
    device_name: str | None = None
    device_firmware: str | None = None
    has_barometer: bool | None = None
    point_count: int | None = None
    sample_interval_sec: float | None = None
    quality_class: str | None = None
    protocol_delta_sec: int | None = None
    # Годится ли трек как измерение трассы (см. track_validation).
    is_course_eligible: bool = True
    exclusion_reason: str | None = None
    exclusion_note: str | None = None
    start_distance_m: float | None = None
    # preview — черновик: человек посмотрел разбор, но ещё не нажал
    # «Сохранить». В профиле и в паспорте трассы такой трек не участвует.
    status: str = "ok"
    created_at: datetime


class TrackDetailResponse(TrackSummaryResponse):
    """Трек целиком: геометрия, метрики трассы и оценка качества записи."""

    quality: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    # [[широта, долгота, секунды от начала записи, высота|null], ...]
    points: list[Any] = Field(default_factory=list)
