from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LocationContactLink(BaseModel):
    id: UUID
    telegram_url: str
    label: str | None = None


class LocationContactPlatform(BaseModel):
    location_id: UUID
    location_name: str
    platform_code: str
    platform_name: str
    is_cancelled: bool
    is_paused: bool


class LocationContactItem(BaseModel):
    group_key: str
    # Локация, на которую вешаются общие для физической точки чат и настройки анонса.
    anchor_location_id: UUID
    display_name: str
    city: str | None = None
    country: str | None = None
    platforms: list[LocationContactPlatform]
    contacts: list[LocationContactLink]
    do_not_disturb: bool
    comment: str | None = None
    updated_at: datetime | None = None


class LocationContactListResponse(BaseModel):
    items: list[LocationContactItem]
    total: int
    with_telegram: int
    do_not_disturb_total: int


class LocationAnnounceSettingsUpdateRequest(BaseModel):
    do_not_disturb: bool = False
    comment: str | None = Field(default=None, max_length=2000)


class LocationAnnounceSettingsResponse(BaseModel):
    location_id: UUID
    do_not_disturb: bool
    comment: str | None = None
    updated_at: datetime


class LocationContactLinkCreateRequest(BaseModel):
    telegram_url: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=128)


class LocationContactLinkUpdateRequest(BaseModel):
    telegram_url: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=128)
