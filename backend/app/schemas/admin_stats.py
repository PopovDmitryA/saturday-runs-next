from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class AdminSiteStatsOverview(BaseModel):
    users_total: int
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


class PageviewRecordRequest(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    authenticated: bool = False
    visitor_key: str | None = Field(default=None, max_length=80)
