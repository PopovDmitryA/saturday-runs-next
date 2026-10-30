"""Ответы админки «Планы» (/api/admin/plans)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class AdminPlansSummary(BaseModel):
    people_total: int
    people_future: int
    plans_future: int
    plans_past: int
    plans_public: int
    series_active: int
    series_sleeping: int
    series_stopped: int
    series_public: int
    series_total: int
    rest_total: int
    rest_future: int
    rest_people: int


class AdminPlansFacts(BaseModel):
    total: int
    ran: int
    volunteered: int
    missed: int
    unknown: int


class AdminPlansDay(BaseModel):
    date: date
    plans: int
    series: int
    people: int
    people_total: int


class AdminPlansLoadDay(BaseModel):
    date: date
    special_title: str | None = None
    people: int
    one_off: int
    regular: int
    locations: int


class AdminPlansBucket(BaseModel):
    label: str
    count: int


class AdminPlansMix(BaseModel):
    only_one_off: int
    only_regular: int
    both: int
    one_off_plans: int
    series: int
    public_plans: int
    private_plans: int
    public_series: int
    private_series: int


class AdminPlansLocation(BaseModel):
    key: str
    name: str
    slug: str | None = None
    upcoming: bool = False
    plans: int = 0
    series: int = 0
    people: int = 0
    next_weeks: int = 0
    count: int = 0


class AdminPlansDeliveries(BaseModel):
    kind: str
    total: int
    period: int
    by_status: dict[str, int] = Field(default_factory=dict)


class AdminPlansOverviewResponse(BaseModel):
    generated_at: datetime
    today: date
    period_days: int
    summary: AdminPlansSummary
    facts: AdminPlansFacts
    daily: list[AdminPlansDay]
    load: list[AdminPlansLoadDay]
    lead_times: list[AdminPlansBucket]
    lead_median_days: float | None = None
    mix: AdminPlansMix
    top_locations: list[AdminPlansLocation]
    locations: list[AdminPlansLocation]
    upcoming_locations: list[AdminPlansLocation]
    deliveries: list[AdminPlansDeliveries]


class AdminPlansFeedItem(BaseModel):
    id: str
    # plan | rest | series | series_stop | series_sleep
    kind: str
    at: datetime | None = None
    user_serial_id: int | None = None
    user_label: str
    profile_private: bool = False
    on_date: date | None = None
    until: date | None = None
    location_name: str | None = None
    location_slug: str | None = None
    is_public: bool
    series_status: str | None = None


class AdminPlansFeedResponse(BaseModel):
    items: list[AdminPlansFeedItem]
    total: int
    limit: int
    offset: int


class AdminPlansDayPerson(BaseModel):
    serial_id: int | None = None
    label: str
    profile_private: bool = False
    # Всего пробежек; None — привязок нет. У «Не бегаю» не считается.
    runs: int | None = None
    regular: bool = False
    is_public: bool = True


class AdminPlansDayLocation(BaseModel):
    key: str
    name: str
    slug: str | None = None
    people: list[AdminPlansDayPerson]


class AdminPlansDaySummary(BaseModel):
    people: int
    locations: int
    one_off: int
    regular: int
    private: int
    rest: int


class AdminPlansDayResponse(BaseModel):
    date: date
    special_title: str | None = None
    is_past: bool
    prev_date: date
    next_date: date
    summary: AdminPlansDaySummary
    locations: list[AdminPlansDayLocation]
    rest: list[AdminPlansDayPerson]
