"""Схемы кабинета организатора локации."""

from __future__ import annotations

from datetime import date
from typing import Any
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
    # Эта площадка — домашняя для человека (общесайтовая логика дома).
    # По отметке страница прячет заезжих, забежавших сюда однажды.
    is_home: bool = False


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
    # Возрастная группа без места в группе («М40-44»).
    age_group: str | None = None
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


class OrganizerPostResponse(BaseModel):
    post_text: str
    template: str = "full"


class MilestoneItem(BaseModel):
    participant_id: str
    name: str | None = None
    profile_url: str | None = None
    # runs_here / runs_platform / vols_here / vols_platform
    kind: str
    kind_label: str
    current: int
    milestone: int
    remaining: int
    last_seen: str | None = None
    last_seen_display: str | None = None


class MilestonesResponse(BaseModel):
    location: OrganizerLocationBrief
    horizon: int
    active_days: int
    items: list[MilestoneItem]
    total: int


class NewcomerItem(BaseModel):
    participant_id: str
    name: str | None = None
    profile_url: str | None = None
    debut_date: date
    debut_date_display: str
    runs_here: int
    runs_total: int
    runs_elsewhere: int
    last_here_display: str | None = None
    last_anywhere_display: str | None = None
    returned_here: bool


class NewcomersResponse(BaseModel):
    location: OrganizerLocationBrief
    days: int
    items: list[NewcomerItem]
    total: int
    # Дебютанты, у которых была возможность вернуться (без последнего события).
    eligible_total: int
    returned_here_total: int
    retention_pct: int | None = None


class BenchRole(BaseModel):
    label: str
    count: int


class BenchItem(BaseModel):
    participant_id: str
    name: str | None = None
    profile_url: str | None = None
    vols_here: int
    vols_total: int
    # Пробежек на этой локации — видно, живой ли это участник или только история.
    runs_here: int = 0
    # Пробежек уже ПОСЛЕ последнего волонтёрства: главный признак того, что
    # человек рядом и его реально дозваться.
    runs_after_last_vol: int = 0
    # None — волонтёрств на локации не было вовсе.
    last_vol_date: date | None = None
    last_vol_display: str | None = None
    missed_events: int | None = None
    roles: list[BenchRole] = Field(default_factory=list)
    last_run_date: date | None = None
    last_run_display: str | None = None
    # never — ни разу не волонтёрил здесь; paused — выпал; active — в строю.
    status: str = "active"
    is_candidate: bool = False


class BenchResponse(BaseModel):
    location: OrganizerLocationBrief
    events_total: int
    min_runs: int
    pause_events: int
    items: list[BenchItem]
    total: int
    candidates_total: int


# ===== Аналитика локации =====


class TeamRoleLoad(BaseModel):
    role_key: str
    role: str
    # Ключевая роль — без неё старт не состоится.
    is_critical: bool
    slots: int
    people: int
    # Сколько человек закрывают 80% волонтёрств: 1 — роль держится на одном человеке.
    bus_factor: int
    top_name: str | None = None
    top_count: int
    top_share_pct: int
    rotation_pct: int
    network_rotation_pct: int | None = None
    rotation_delta_pct: int | None = None


class TeamLoadPerson(BaseModel):
    participant_id: str
    name: str | None = None
    profile_url: str | None = None
    slots: int
    share_pct: int


class TeamLoadResponse(BaseModel):
    location: OrganizerLocationBrief
    months: int
    events_total: int
    volunteers_total: int
    slots_total: int
    avg_per_event: float | None = None
    top_load: list[TeamLoadPerson] = Field(default_factory=list)
    roles: list[TeamRoleLoad] = Field(default_factory=list)


class AttendanceEvent(BaseModel):
    date: date
    date_display: str
    event_number: int | None = None
    platform_code: str
    finishers: int
    volunteers: int


class AttendanceMonth(BaseModel):
    month: str
    events: int
    avg_finishers: float
    max_finishers: int
    # Платформа большинства стартов месяца — окраска эр parkrun/5в на графике.
    platform_code: str


class AttendanceResponse(BaseModel):
    location: OrganizerLocationBrief
    events: list[AttendanceEvent] = Field(default_factory=list)
    months: list[AttendanceMonth] = Field(default_factory=list)
    events_total: int
    last_12m_avg: float | None = None
    prev_12m_avg: float | None = None
    yoy_delta_pct: int | None = None
    record_finishers: int | None = None
    record_date: str | None = None


class AudienceAgeGroup(BaseModel):
    group: str
    finishes: int
    share_pct: float


class AudienceGender(BaseModel):
    label: str
    finishes: int
    share_pct: float


class AudienceClub(BaseModel):
    club: str
    people: int
    finishes: int


class AudienceResponse(BaseModel):
    location: OrganizerLocationBrief
    months: int
    finishes_total: int
    people_total: int
    age_groups: list[AudienceAgeGroup] = Field(default_factory=list)
    genders: list[AudienceGender] = Field(default_factory=list)
    clubs: list[AudienceClub] = Field(default_factory=list)


class BenchmarkMetric(BaseModel):
    key: str
    label: str
    our_value: float
    median: float | None = None
    best: float | None = None
    rank: int | None = None
    peers: int
    delta_vs_median_pct: int | None = None


class BenchmarkPeer(BaseModel):
    location_id: str
    name: str
    city: str | None = None
    region: str | None = None
    events: int
    avg_finishers: float
    avg_volunteers: float
    unique_runners: int
    unique_volunteers: int
    female_share_pct: float
    volunteer_rotation_pct: int
    is_ours: bool = False


class BenchmarkResponse(BaseModel):
    location: OrganizerLocationBrief
    months: int
    scope: str
    scope_label: str
    peers_total: int
    # Размер выборки каждого скоупа: фронт скрывает вкладки, где сравнивать не с кем.
    scope_sizes: dict[str, int] = Field(default_factory=dict)
    metrics: list[BenchmarkMetric] = Field(default_factory=list)
    peers: list[BenchmarkPeer] = Field(default_factory=list)


class ProtocolRevisionItem(BaseModel):
    detected_at: str
    kind: str
    details: dict[str, Any] = Field(default_factory=dict)


class ProtocolTimelineItem(BaseModel):
    date: date
    date_display: str
    event_number: int | None = None
    start_time: str | None = None
    finishers: int
    last_finish_display: str | None = None
    first_seen_at: str | None = None
    first_seen_display: str | None = None
    delay_hours: float | None = None
    # Светофор: green ≤3ч от финиша последнего, yellow — день в день, red — позже.
    level: str | None = None
    directors: list[str] = Field(default_factory=list)
    revisions: list[ProtocolRevisionItem] = Field(default_factory=list)


class ProtocolTimelineResponse(BaseModel):
    location: OrganizerLocationBrief
    # False — у идентичности нет 5в-половины: наблюдатель пока умеет только 5 вёрст.
    supported: bool
    tz_offset_moscow: int
    items: list[ProtocolTimelineItem] = Field(default_factory=list)
    median_delay_hours_12m: float | None = None
    network_rank: int | None = None
    network_size: int | None = None


class HealthIndicator(BaseModel):
    key: str
    title: str
    level: str | None = None
    value_display: str | None = None
    # Что это за показатель — и как оргкоманде его улучшить.
    hint: str
    advice: str | None = None


class LocationHealthResponse(BaseModel):
    location: OrganizerLocationBrief
    indicators: list[HealthIndicator] = Field(default_factory=list)
