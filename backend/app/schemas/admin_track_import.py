"""Схемы импорта треков из админки."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TrackImportProblem(BaseModel):
    name: str
    reason: str


class TrackImportItem(BaseModel):
    """Строка предпросмотра: один разобранный трек."""

    track_id: UUID
    source: str
    source_ref: str
    started_at: datetime | None = None
    distance_m: float | None = None
    duration_sec: int | None = None
    elevation_gain_m: float | None = None
    # Есть ли высоты по точкам: без них не будет профиля рельефа.
    has_elevation_profile: bool = False
    device_name: str | None = None
    quality_class: str | None = None
    lap_count: int | None = None
    # К какой пробежке привязался трек: локация и дата старта из протокола.
    location_name: str | None = None
    event_date: date | None = None
    matched_run: bool = False
    protocol_delta_sec: int | None = None
    is_course_eligible: bool = True
    exclusion_reason: str | None = None
    exclusion_note: str | None = None
    # Предлагаем ли взять этот трек по умолчанию и почему нет. Галочку в
    # предпросмотре админ может переставить руками — это только подсказка.
    suggested: bool = True
    suggestion_note: str | None = None


class TrackImportBatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    target_user_id: UUID
    target_user_name: str | None = None
    source_kind: str
    status: str
    total_count: int
    processed_count: int
    imported_count: int
    skipped_count: int
    pending_count: int = 0
    problems: list[TrackImportProblem] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    applied_at: datetime | None = None


class TrackImportBatchDetail(TrackImportBatch):
    items: list[TrackImportItem] = Field(default_factory=list)


class TrackImportListResponse(BaseModel):
    items: list[TrackImportBatch] = Field(default_factory=list)


class TrackImportApplyRequest(BaseModel):
    """Что именно админ отметил галочками в предпросмотре."""

    # None — подтвердить всё разобранное (поведение до появления галочек).
    track_ids: list[UUID] | None = None
