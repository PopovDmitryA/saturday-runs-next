from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class CanonicalParticipant:
    external_user_id: str
    display_name: str
    profile_url: str
    total_runs: int | None = None
    total_volunteering: int | None = None
    club_name: str | None = None
    barcode_id: str | None = None
    planning_location: str | None = None
    source_url: str = ""


@dataclass
class ProfilePreviewActivity:
    kind: str
    event_date: date
    location_name: str
    finish_time_display: str | None = None
    role: str | None = None


@dataclass
class ProfilePreview:
    external_user_id: str
    display_name: str
    profile_url: str
    total_runs: int | None = None
    total_volunteering: int | None = None
    club_name: str | None = None
    barcode_id: str | None = None
    planning_location: str | None = None
    planning_location_seen_at: datetime | None = None
    age_category: str | None = None
    parkrun_eligible: bool = False
    platform_code: str = ""
    recent_activities: list[ProfilePreviewActivity] = field(default_factory=list)
    data_source: str = "live"
    data_updated_at: datetime | None = None
    data_through_date: date | None = None


@dataclass
class CanonicalLocation:
    external_key: str
    name: str
    country: str | None = None
    city: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_url: str = ""
    course_source_url: str = ""
    map_url: str | None = None


@dataclass
class CanonicalEvent:
    external_event_key: str
    event_date: date
    location_external_key: str
    location_name: str
    title: str | None = None
    event_number: int | None = None
    is_test_event: bool = False
    source_url: str = ""


@dataclass
class CanonicalEventSummary:
    external_event_key: str
    event_date: date
    event_number: int | None
    location_external_key: str
    location_name: str
    finishers_count: int | None = None
    volunteers_count: int | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    is_test_event: bool = False
    source_url: str = ""
    summary_hash: str = ""
    summary_extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalRunResult:
    external_result_key: str
    event_date: date
    external_user_id: str
    participant_name: str
    position: int | None
    finish_time_sec: int | None
    finish_time_display: str | None
    pace_sec_per_km: int | None = None
    pace_display: str | None = None
    age_category: str | None = None
    status: str | None = None
    is_pr: bool = False
    is_first_run: bool = False
    is_first_run_at_location: bool = False
    club_name: str | None = None
    achievement_labels: list[str] = field(default_factory=list)
    location_external_key: str = ""
    location_name: str = ""
    event_number: int | None = None


@dataclass
class CanonicalVolunteerResult:
    external_result_key: str
    event_date: date
    external_user_id: str | None
    participant_name: str | None
    role: str
    source_url: str = ""
    location_external_key: str = ""
    location_name: str = ""
    event_number: int | None = None


@dataclass
class ExternalEventRef:
    platform_code: str
    external_event_key: str
    source_url: str


@dataclass
class FetchResult:
    source_url: str
    source_hash: str
    fetched_at: datetime = field(default_factory=datetime.utcnow)
