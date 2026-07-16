from datetime import date

from pydantic import BaseModel, Field


class VolunteerRoleCountResponse(BaseModel):
    role: str
    count: int


class MapLocationPlatformVisitResponse(BaseModel):
    platform_code: str
    location_name: str
    location_url: str | None = None
    run_dates: list[date] = Field(default_factory=list)
    volunteer_dates: list[date] = Field(default_factory=list)
    volunteer_roles: list[VolunteerRoleCountResponse] = Field(default_factory=list)


class MapLocationPointResponse(BaseModel):
    id: str
    catalog_identity_key: str | None = None
    location_slug: str | None = None
    name: str
    latitude: float
    longitude: float
    city: str | None = None
    region: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    active_platform: str | None = None
    location_url: str | None = None
    is_paused: bool = False
    is_cancelled: bool = False
    run_count: int = 0
    volunteer_count: int = 0
    visit_count: int = 0
    last_visit_date: date | None = None
    run_dates: list[date] = Field(default_factory=list)
    volunteer_dates: list[date] = Field(default_factory=list)
    platform_visits: list[MapLocationPlatformVisitResponse] = Field(default_factory=list)


class MapLocationsResponse(BaseModel):
    points: list[MapLocationPointResponse] = Field(default_factory=list)
    total_locations: int = 0
    mapped_locations: int = 0
    unmapped_locations: int = 0


class UniqueLocationPlatformDetailResponse(BaseModel):
    platform_code: str
    location_name: str
    run_dates: list[date] = Field(default_factory=list)
    volunteer_dates: list[date] = Field(default_factory=list)
    volunteer_roles: list[VolunteerRoleCountResponse] = Field(default_factory=list)


class UniqueLocationDetailResponse(BaseModel):
    catalog_identity_key: str
    location_slug: str | None = None
    name: str
    city: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    has_coordinates: bool = False
    is_paused: bool = False
    is_cancelled: bool = False
    run_count: int = 0
    volunteer_count: int = 0
    first_visit_date: date | None = None
    last_visit_date: date | None = None
    platforms: list[UniqueLocationPlatformDetailResponse] = Field(default_factory=list)


class UniqueLocationsPlatformSummaryResponse(BaseModel):
    platform_code: str
    location_count: int


class UniqueLocationsDetailResponse(BaseModel):
    locations: list[UniqueLocationDetailResponse] = Field(default_factory=list)
    total_locations: int = 0
    unique_run_locations: int = 0
    unique_volunteer_locations: int = 0
    mapped_locations: int = 0
    unmapped_locations: int = 0
    platform_summary: list[UniqueLocationsPlatformSummaryResponse] = Field(default_factory=list)


class LocationPagePlatformResponse(BaseModel):
    platform_code: str
    location_name: str
    external_key: str
    url: str | None = None
    first_event_date: date | None = None
    last_event_date: date | None = None
    events_count: int = 0
    is_active: bool | None = None


class LocationCourseRecordResponse(BaseModel):
    finish_time_sec: int
    finish_time_display: str
    runner_name: str | None = None
    event_date: date | None = None
    platform_code: str | None = None


class LocationAttendanceRecordResponse(BaseModel):
    finishers: int
    event_date: date | None = None
    event_number: int | None = None
    platform_code: str | None = None


class LocationCourseRecordsResponse(BaseModel):
    male: LocationCourseRecordResponse | None = None
    female: LocationCourseRecordResponse | None = None


class LocationLastEventResponse(BaseModel):
    event_date: date
    platform_code: str
    finishers: int | None = None
    volunteers: int | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None


class LocationPageStatsResponse(BaseModel):
    events_count: int = 0
    finishers_total: int = 0
    unique_participants: int = 0
    volunteers_total: int = 0
    unique_volunteers: int = 0
    avg_finish_time_sec: int | None = None
    avg_finish_time_display: str | None = None
    avg_finishers: int | None = None
    attendance_record: LocationAttendanceRecordResponse | None = None
    course_records: LocationCourseRecordsResponse = Field(default_factory=LocationCourseRecordsResponse)
    first_event_date: date | None = None
    last_event_date: date | None = None
    median_finish_time_sec: int | None = None
    median_finish_time_display: str | None = None
    last_event: LocationLastEventResponse | None = None
    # Насколько последний старт сдвинул агрегат по сравнению со всеми
    # предыдущими (avg_after - avg_before); отрицательное значение — быстрее.
    avg_finish_time_delta_sec: int | None = None
    median_finish_time_delta_sec: int | None = None


class LocationHistogramRowResponse(BaseModel):
    start_sec: int
    gender: str | None = None
    age_group: str | None = None
    count: int


class LocationHistogramResponse(BaseModel):
    bin_size_sec: int
    rows: list[LocationHistogramRowResponse] = Field(default_factory=list)


class LocationPageResponse(BaseModel):
    slug: str
    identity_key: str
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    is_paused: bool = False
    is_cancelled: bool = False
    latitude: float | None = None
    longitude: float | None = None
    map_url: str | None = None
    start_point_url: str | None = None
    platforms: list[LocationPagePlatformResponse] = Field(default_factory=list)
    stats: LocationPageStatsResponse = Field(default_factory=LocationPageStatsResponse)
    histogram: LocationHistogramResponse = Field(default_factory=lambda: LocationHistogramResponse(bin_size_sec=10))


class LocationEventRowResponse(BaseModel):
    event_date: date
    platform_code: str
    event_number: int | None = None
    overall_number: int
    finishers: int | None = None
    volunteers: int | None = None
    best_male_time_sec: int | None = None
    best_male_time_display: str | None = None
    best_female_time_sec: int | None = None
    best_female_time_display: str | None = None
    avg_time_sec: int | None = None
    avg_time_display: str | None = None
    debutants: int | None = None
    first_at_location: int | None = None
    prs: int | None = None
    has_protocol: bool = False
    protocol_url: str | None = None
    is_attendance_record: bool = False
    is_course_record_male: bool = False
    is_course_record_female: bool = False
    is_platform_attendance_record: bool = False
    is_platform_course_record_male: bool = False
    is_platform_course_record_female: bool = False


class LocationEventsResponse(BaseModel):
    slug: str
    name: str
    total: int = 0
    items: list[LocationEventRowResponse] = Field(default_factory=list)


class LocationLeaderRunnerResponse(BaseModel):
    name: str | None = None
    runs_count: int
    best_time_sec: int | None = None
    best_time_display: str | None = None


class LocationLeaderVolunteerResponse(BaseModel):
    name: str | None = None
    count: int


class LocationLeadersResponse(BaseModel):
    slug: str
    name: str
    runners: list[LocationLeaderRunnerResponse] = Field(default_factory=list)
    volunteers: list[LocationLeaderVolunteerResponse] = Field(default_factory=list)


class LocationIndexItemResponse(BaseModel):
    slug: str
    identity_key: str
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    is_paused: bool = False
    is_cancelled: bool = False
    events_count: int = 0
    finishers_total: int = 0
    first_event_date: date | None = None
    last_event_date: date | None = None


class LocationsIndexResponse(BaseModel):
    items: list[LocationIndexItemResponse] = Field(default_factory=list)
    total: int = 0


class CatalogLocationTableRowResponse(BaseModel):
    row_key: str
    catalog_identity_key: str
    location_id: str
    location_slug: str | None = None
    name: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    platform_code: str
    is_paused: bool = False
    is_cancelled: bool = False
    has_coordinates: bool = False
    location_url: str | None = None
    visited: bool = False
    first_visit_date: date | None = None


class CatalogLocationsTableResponse(BaseModel):
    rows: list[CatalogLocationTableRowResponse] = Field(default_factory=list)
    total_rows: int = 0
