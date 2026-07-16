from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from app.schemas.location_contacts import LocationContactLink


class EventReportLocationItem(BaseModel):
    location_id: UUID
    location_name: str
    city: str | None = None
    platform_code: str
    platform_name: str
    events_count: int
    last_event_date: date


class EventReportLocationsResponse(BaseModel):
    items: list[EventReportLocationItem]


class EventReportDateItem(BaseModel):
    event_id: UUID
    event_date: date
    event_number: int | None = None
    finishers_count: int


class EventReportDatesResponse(BaseModel):
    items: list[EventReportDateItem]


class ReportPerson(BaseModel):
    participant_id: UUID | None = None
    name: str | None = None
    profile_url: str | None = None


class ReportCountedPerson(ReportPerson):
    count: int


class ReportOneStepPerson(ReportCountedPerson):
    next_milestone: int


class ReportEventInfo(BaseModel):
    event_id: UUID
    event_date: date
    event_number: int | None = None
    location_id: UUID
    location_name: str
    canonical_name: str | None = None
    platform_code: str
    platform_name: str
    source_url: str | None = None
    events_total_at_location: int
    telegram_contacts: list[LocationContactLink]
    do_not_disturb: bool


class ReportHeader(BaseModel):
    finishers: int
    volunteers: int
    avg_time_sec: int | None = None
    best_male_sec: int | None = None
    best_female_sec: int | None = None
    previous_event_date: date | None = None
    previous_event_finishers: int | None = None
    attendance_record: bool
    prior_max_finishers: int | None = None


class ReportCourseRecord(ReportPerson):
    gender: str
    time_sec: int
    previous_sec: int


class ReportTopFinish(ReportPerson):
    position: int | None = None
    finish_time_sec: int | None = None
    finish_time_display: str
    age_category: str | None = None
    gender: str | None = None
    gender_rank: int
    gender_total: int
    age_rank: int
    age_total: int
    gender_qualified: bool
    age_qualified: bool


class ReportStats(BaseModel):
    newcomers: list[ReportPerson]
    guests: list[ReportPerson]
    personal_bests: list[ReportPerson]
    location_bests: list[ReportPerson]
    comebacks: list[ReportPerson]
    first_volunteers: list[ReportPerson]
    first_volunteers_at_location: list[ReportPerson]
    new_role_volunteers: list[ReportPerson]


class ReportRunsAndVolunteering(BaseModel):
    runs: list[ReportCountedPerson]
    volunteering: list[ReportCountedPerson]


class ReportOneStep(BaseModel):
    runs: list[ReportOneStepPerson]
    volunteering: list[ReportOneStepPerson]


class EventReportResponse(BaseModel):
    event: ReportEventInfo
    header: ReportHeader
    course_records: list[ReportCourseRecord]
    top_finishes: list[ReportTopFinish]
    stats: ReportStats
    location_milestones: ReportRunsAndVolunteering
    one_step: ReportOneStep
    clubs: ReportRunsAndVolunteering
    global_run_jubilees: list[ReportCountedPerson]
    post_text: str
