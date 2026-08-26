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


class AdminUserHomeLocationCandidate(BaseModel):
    identity_key: str
    name: str
    city: str | None = None
    slug: str | None = None
    run_days: int = 0
    volunteer_days: int = 0


class AdminUserHomeLocation(BaseModel):
    """Предполагаемый «дом» участника — та же площадка, что видит он сам.

    is_manual — выбрал руками в настройках, иначе определено автоматически.
    is_tie — правило отбора исчерпано и площадки поделили первое место: тогда
    в tied лежат все претенденты.
    """

    identity_key: str
    name: str
    slug: str | None = None
    city: str | None = None
    region: str | None = None
    run_days: int = 0
    volunteer_days: int = 0
    is_manual: bool = False
    is_tie: bool = False
    tied: list[AdminUserHomeLocationCandidate] = Field(default_factory=list)
    locations_total: int = 0


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
    home_location: AdminUserHomeLocation | None = None


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
    # Оригинал аватарки — раскрывается по клику на неё (29.07.2026).
    avatar_full_url: str | None = None


class AdminUserPreviewDashboardResponse(BaseModel):
    user: AdminUserPreviewUser
    stats: dict[str, object]
    computed_at: datetime | None = None
    platform_links: list[AdminPlatformLinkBrief] = Field(default_factory=list)




class AdminOrganizerGrantItem(BaseModel):
    id: str
    location_key: str
    # Имя/слаг из каталога локаций; None, если ключ в каталоге не нашёлся
    # (например, локация исчезла из выгрузки).
    location_name: str | None = None
    location_slug: str | None = None
    note: str | None = None
    created_at: datetime


class AdminOrganizerDerivedItem(BaseModel):
    location_key: str
    location_name: str | None = None
    location_slug: str | None = None


class AdminOrganizerAccessResponse(BaseModel):
    manual: list[AdminOrganizerGrantItem] = Field(default_factory=list)
    derived: list[AdminOrganizerDerivedItem] = Field(default_factory=list)


class AdminOrganizerGrantCreate(BaseModel):
    location_key: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=500)
