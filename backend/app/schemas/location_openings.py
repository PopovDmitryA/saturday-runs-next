from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LocationOpeningEvent(BaseModel):
    """Старт площадки в подсказке админки: номер, дата и сколько финишировало."""

    event_id: UUID
    event_number: int | None = None
    event_date: date
    title: str | None = None
    source_url: str | None = None
    finishers: int | None = None
    # Именно этот старт сейчас считается открытием площадки.
    is_opening: bool = False


class LocationOpeningItem(BaseModel):
    location_id: UUID
    location_name: str
    location_city: str | None = None
    external_key: str
    source_url: str | None = None
    platform_code: str
    # Номер старта-открытия; None — открытия у площадки нет.
    opening_event_number: int | None = None
    # manual — задано руками, auto — событие №1 из протокола, none — открытия нет.
    opening_source: str
    opening_event: LocationOpeningEvent | None = None
    # Номер задан, а события с таким номером у площадки нет (опечатка в разметке).
    opening_event_missing: bool = False
    note: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None
    first_events: list[LocationOpeningEvent] = []


class LocationOpeningListResponse(BaseModel):
    platform: str
    items: list[LocationOpeningItem]
    total: int
    with_opening: int
    manual_total: int
    # Системе нужна ручная разметка (С95): без неё открытий у неё нет вовсе.
    needs_manual: bool = False


class LocationOpeningUpdateRequest(BaseModel):
    # Пустой номер — осознанное «открытия нет», а не «не знаю»: он гасит
    # автоправило системы (событие №1).
    opening_event_number: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=512)


class LocationOpeningResponse(BaseModel):
    location_id: UUID
    location_name: str
    platform_code: str
    opening_event_number: int | None = None
    opening_source: str
    opening_event: LocationOpeningEvent | None = None
    opening_event_missing: bool = False
    note: str | None = None
    updated_at: datetime | None = None
