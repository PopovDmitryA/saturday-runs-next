from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from app.schemas.location_contacts import LocationContactLink


class DigestDateItem(BaseModel):
    event_date: date
    events_count: int
    locations_count: int


class DigestDatesResponse(BaseModel):
    items: list[DigestDateItem]


class DigestCourseRecord(BaseModel):
    gender: str
    record_sec: int
    record_name: str | None = None
    previous_sec: int | None = None
    previous_date: date | None = None
    previous_name: str | None = None
    years_since: float


class DigestAgeRecord(BaseModel):
    age_group: str | None = None
    record_sec: int
    record_name: str | None = None
    previous_sec: int | None = None
    previous_date: date | None = None
    previous_name: str | None = None
    group_total: int | None = None
    years_since: float


class DigestAttendanceRecord(BaseModel):
    finishers: int
    prior_max: int


class DigestMilestonePerson(BaseModel):
    name: str | None = None
    count: int


class DigestMilestones(BaseModel):
    runs: list[DigestMilestonePerson]
    volunteering: list[DigestMilestonePerson]


class DigestCard(BaseModel):
    location_id: UUID
    location_name: str
    city: str | None = None
    country: str | None = None
    location_url: str | None = None
    platform_code: str
    platform_name: str
    telegram_contacts: list[LocationContactLink]
    do_not_disturb: bool
    comment: str | None = None
    score: int
    importance: str
    course_records: list[DigestCourseRecord]
    age_records: list[DigestAgeRecord]
    attendance_record: DigestAttendanceRecord | None = None
    milestones: DigestMilestones
    post_text: str


class RecordsDigestResponse(BaseModel):
    event_date: date
    generated_locations: int
    with_telegram: int
    without_telegram: int
    do_not_disturb: int
    cards: list[DigestCard]
