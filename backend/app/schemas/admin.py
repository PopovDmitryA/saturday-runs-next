from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AdminPlatformLinkBrief(BaseModel):
    platform_code: str
    external_user_id: str
    external_url: str
    display_name: str | None = None
    sync_status: str


class AdminUserAuthBrief(BaseModel):
    provider: str
    label: str
    external_id: str


class AdminUserListItem(BaseModel):
    id: str
    telegram_id: int | None = None
    telegram_username: str | None = None
    display_name: str | None = None
    auth_logins: list[AdminUserAuthBrief] = Field(default_factory=list)
    news_subscribed: bool = False
    consent_accepted: bool = False
    created_at: datetime
    last_login_at: datetime | None = None
    total_runs: int | None = None
    total_volunteering: int | None = None
    platform_links: list[AdminPlatformLinkBrief] = Field(default_factory=list)


class AdminUserListResponse(BaseModel):
    items: list[AdminUserListItem]
    total: int
    limit: int
    offset: int
    query: str | None = None


class AdminS95ParticipantListItem(BaseModel):
    id: str
    external_user_id: str
    display_name: str | None = None
    profile_url: str | None = None
    barcode_id: str | None = None
    club_name: str | None = None
    planning_location: str | None = None
    planning_location_seen_at: datetime | None = None
    fetched_at: datetime | None = None
    sync_status: str
    error_message: str | None = None


class AdminS95ParticipantListResponse(BaseModel):
    items: list[AdminS95ParticipantListItem]
    total: int
    limit: int
    offset: int
    query: str | None = None


class AdminUserPreviewUser(BaseModel):
    id: str
    telegram_id: int | None = None
    telegram_username: str | None = None
    auth_logins: list[AdminUserAuthBrief] = Field(default_factory=list)
    display_name: str | None = None
    news_subscribed: bool = False


class AdminUserPreviewDashboardResponse(BaseModel):
    user: AdminUserPreviewUser
    stats: dict[str, object]
    computed_at: datetime | None = None
    platform_links: list[AdminPlatformLinkBrief] = Field(default_factory=list)
