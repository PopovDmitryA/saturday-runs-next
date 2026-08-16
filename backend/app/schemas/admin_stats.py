from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AdminSiteStatsOverview(BaseModel):
    users_total: int
    users_profile_public: int
    users_profile_private: int
    users_with_consent: int
    users_active_period: int
    users_new_period: int = 0
    users_with_any_link: int
    users_with_all_three_links: int
    platform_links_total: int
    links_new_period: int = 0
    links_by_platform: dict[str, int]
    pageviews_period: int = 0
    unique_visitors_period: int = 0
    logins_period: int = 0
    participants_total: int
    events_total: int
    run_results_total: int
    locations_total: int
    sync_jobs_total: int
    sync_jobs_active: int
    login_requests_period: int


class AdminSiteStatsDayPoint(BaseModel):
    date: date
    value: int


class AdminSiteStatsPageviewsDay(BaseModel):
    date: date
    total: int = 0
    unique_visitors: int = 0
    landing: int = 0
    demo: int = 0
    app: int = 0
    login: int = 0
    about: int = 0
    admin: int = 0
    other: int = 0
    authenticated: int = 0
    anonymous: int = 0


class AdminSiteStatsResponse(BaseModel):
    period_days: int
    generated_at: datetime
    overview: AdminSiteStatsOverview
    users_new_by_day: list[AdminSiteStatsDayPoint]
    links_new_by_day: list[AdminSiteStatsDayPoint]
    logins_by_day: list[AdminSiteStatsDayPoint]
    login_requests_by_day: list[AdminSiteStatsDayPoint]
    pageviews_by_day: list[AdminSiteStatsPageviewsDay]


class AdminGeographyCityRow(BaseModel):
    city: str
    region: str | None = None
    users: int = 0
    users_new_period: int = 0
    # Сколько разных площадок города стали кому-то домашними.
    locations: int = 0


class AdminGeographyLocationRow(BaseModel):
    identity_key: str
    name: str
    slug: str | None = None
    city: str | None = None
    region: str | None = None
    users: int = 0
    users_new_period: int = 0


class AdminUsersGeographyResponse(BaseModel):
    """Регистрации в разрезе городов и площадок — «где работает сарафанка».

    Город и площадка берутся из домашней локации пользователя; у кого пробежек
    в базе нет, дома нет тоже — такие идут в users_without_home.
    """

    generated_at: datetime
    period_days: int
    users_total: int
    users_new_period: int = 0
    users_with_home: int = 0
    users_new_with_home: int = 0
    users_without_home: int = 0
    users_without_links: int = 0
    cities_total: int = 0
    locations_total: int = 0
    cities: list[AdminGeographyCityRow] = Field(default_factory=list)
    locations: list[AdminGeographyLocationRow] = Field(default_factory=list)


class PageviewRecordRequest(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    authenticated: bool = False
    visitor_key: str | None = Field(default=None, max_length=80)
    # Клиентский id просмотра — связывает событие с беконом pageleave (длительность).
    view_id: UUID | None = None


class PageleaveRecordRequest(BaseModel):
    view_id: UUID
    duration_sec: int = Field(ge=0, le=86400)


class AbEventRecordRequest(BaseModel):
    experiment: str = Field(min_length=1, max_length=32)
    variant: str = Field(min_length=1, max_length=8)
    visitor_key: str = Field(min_length=1, max_length=80)
    event_type: str = Field(min_length=1, max_length=32)
    value: str = Field(default="", max_length=128)
    path: str = Field(default="", max_length=256)


class PageAnalyticsRowStats(BaseModel):
    views: int
    unique_viewers: int
    self_views: int
    avg_duration_sec: int | None


class PageAnalyticsSection(PageAnalyticsRowStats):
    page_type: str


class PageAnalyticsEntity(PageAnalyticsRowStats):
    entity_key: str
    label: str
    href: str | None


class HomeAbVariantStats(BaseModel):
    """Показы одного варианта главной. Воронка считается офлайн."""

    variant: str
    views: int
    viewers: int


class HomeLinkClickStats(BaseModel):
    """Переход по ссылке с главной: локация или профиль участника."""

    kind: str
    entity_key: str
    label: str
    href: str | None
    clicks: int
    visitors: int


class ShareFunnelRow(BaseModel):
    """Ступень воронки шаринга: событие, сколько раз и сколько посетителей."""

    event_type: str
    events: int
    visitors: int


class SharePairRow(BaseModel):
    """Пара «сюжет + вход»: где именно жмут «Поделиться» и как часто."""

    subject: str
    entry: str
    shown: int
    opens: int


class ShareChannelRow(BaseModel):
    """Успешные шеринги по каналам: system / download / copy."""

    channel: str
    successes: int


class ShareCountRow(BaseModel):
    """Счётчик выбора: фон (look) или формат."""

    value: str
    count: int


class ShareStats(BaseModel):
    funnel: list[ShareFunnelRow] = Field(default_factory=list)
    pairs: list[SharePairRow] = Field(default_factory=list)
    channels: list[ShareChannelRow] = Field(default_factory=list)
    looks: list[ShareCountRow] = Field(default_factory=list)
    formats: list[ShareCountRow] = Field(default_factory=list)
    photo_added: int = 0


class OgFetchRow(BaseModel):
    """Разворачивание ссылки ботом: страница и сколько раз запросили превью."""

    page_type: str
    entity_key: str
    label: str
    href: str | None
    fetches: int
    bots: int


class PageAnalyticsResponse(BaseModel):
    # Границы включительно; сервер отдаёт их разрешёнными (в т.ч. когда клиент
    # прислал period_days), чтобы UI показывал ровно то, что посчитано.
    date_from: date
    date_to: date
    generated_at: datetime
    sections: list[PageAnalyticsSection]
    home_ab: list[HomeAbVariantStats] = Field(default_factory=list)
    home_links: list[HomeLinkClickStats] = Field(default_factory=list)
    share: ShareStats = Field(default_factory=ShareStats)
    og_fetches: list[OgFetchRow] = Field(default_factory=list)
    top_profiles: list[PageAnalyticsEntity]
    top_locations: list[PageAnalyticsEntity]
