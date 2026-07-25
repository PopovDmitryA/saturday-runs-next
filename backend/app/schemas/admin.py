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


class AdminLoginEventItem(BaseModel):
    ts: datetime
    event_type: str
    provider: str = ""
    ip: str = ""
    user_agent: str = ""
    device_ref: str = ""
    session_ref: str = ""


class AdminLoginEventsResponse(BaseModel):
    """Журнал входов одного пользователя + сводка «рвутся ли сессии сами».

    unexpected_relogins — входы с устройства, с которого не выходили:
    признак того, что авторизация слетела без участия пользователя.
    """

    items: list[AdminLoginEventItem]
    logins: int
    logouts: int
    devices: int
    unexpected_relogins: int


class AdminUserPreviewUser(BaseModel):
    id: str
    telegram_id: int | None = None
    telegram_username: str | None = None
    auth_logins: list[AdminUserAuthBrief] = Field(default_factory=list)
    display_name: str | None = None
    news_subscribed: bool = False
    # Аватар участника — виден и гостю на публичном профиле (26.07.2026).
    avatar_url: str | None = None


class AdminUserPreviewDashboardResponse(BaseModel):
    user: AdminUserPreviewUser
    stats: dict[str, object]
    computed_at: datetime | None = None
    platform_links: list[AdminPlatformLinkBrief] = Field(default_factory=list)


