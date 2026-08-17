"""Схемы кабинета организатора локации."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class OrganizerLocationItem(BaseModel):
    location_key: str
    slug: str
    name: str
    city: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    is_paused: bool = False
    # volunteering — автодоступ по волонтёрствам, manual — ручной грант, both — и то и то.
    access_source: str


class OrganizerLocationsResponse(BaseModel):
    items: list[OrganizerLocationItem]
    total: int


class OrganizerLocationBrief(BaseModel):
    slug: str
    name: str


class AbsenceItem(BaseModel):
    name: str | None = None
    # Публичный адрес профиля на сайте (public_slug или serial_id) — есть только
    # у привязавших аккаунт.
    handle: str | None = None
    last_date: date
    last_date_display: str
    runs_here: int
    runs_total: int
    missed_events: int


class AbsenceResponse(BaseModel):
    location: OrganizerLocationBrief
    min_runs: int
    min_missed: int
    events_total: int
    items: list[AbsenceItem]
    total: int


class OrganizerEventDateItem(BaseModel):
    event_id: UUID
    event_date: date
    event_number: int | None = None
    platform_code: str
    finishers_count: int


class OrganizerEventDatesResponse(BaseModel):
    location: OrganizerLocationBrief
    items: list[OrganizerEventDateItem]


class SvodEventInfo(BaseModel):
    event_id: UUID
    event_date: date
    event_number: int | None = None
    location_id: UUID
    location_name: str
    platform_code: str
    platform_name: str
    source_url: str | None = None
    finishers_count: int
    volunteers_count: int


class SvodRunnerRow(BaseModel):
    position: int | None = None
    participant_id: UUID | None = None
    name: str | None = None
    profile_url: str | None = None
    finish_time_sec: int | None = None
    finish_time_display: str
    first_in_system: bool
    first_at_location: bool
    is_pb: bool
    is_location_pb: bool
    # Вернулся после паузы больше года.
    comeback: bool
    location_runs_count: int
    platform_runs_count: int
    location_milestone: int | None = None
    location_next_milestone: int | None = None
    platform_milestone: int | None = None
    platform_next_milestone: int | None = None


class SvodVolunteerRole(BaseModel):
    label: str
    count: int
    milestone: int | None = None


class SvodVolunteerRow(BaseModel):
    participant_id: UUID | None = None
    name: str | None = None
    profile_url: str | None = None
    roles: list[SvodVolunteerRole] = Field(default_factory=list)
    new_roles: list[str] = Field(default_factory=list)
    first_volunteering: bool
    first_at_location: bool
    location_vol_count: int
    platform_vol_count: int
    location_milestone: int | None = None
    location_next_milestone: int | None = None
    platform_milestone: int | None = None
    platform_next_milestone: int | None = None


class SvodResponse(BaseModel):
    event: SvodEventInfo
    runners: list[SvodRunnerRow]
    volunteers: list[SvodVolunteerRow]
