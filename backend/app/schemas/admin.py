from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AdminPlatformLinkBrief(BaseModel):
    platform_code: str
    external_user_id: str
    external_url: str
    display_name: str | None = None
    sync_status: str
    run_count: int = 0
    volunteer_count: int = 0
    barcode_id: str | None = None


class AdminUserAuthBrief(BaseModel):
    provider: str
    label: str
    external_id: str


class AdminUserListItem(BaseModel):
    id: str
    serial_id: int | None = None
    telegram_id: int | None = None
    telegram_username: str | None = None
    display_name: str | None = None
    public_slug: str | None = None
    profile_private: bool = False
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


class HistoryMilestoneKindSettingResponse(BaseModel):
    kind: str
    label: str
    description: str
    enabled: bool


class HistoryMilestoneKindSettingsResponse(BaseModel):
    kinds: list[HistoryMilestoneKindSettingResponse] = Field(default_factory=list)


class HistoryMilestoneKindUpdateRequest(BaseModel):
    enabled: bool
