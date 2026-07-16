from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class PortalHeroResponse(BaseModel):
    finishes_total: int
    participants_total: int
    locations_total: int
    starts_total: int


class PortalPulseResponse(BaseModel):
    event_date: date
    starts: int
    finishes: int
    newcomers: int
    volunteers: int
    personal_records: int


class PortalAttendanceRecordResponse(BaseModel):
    location_name: str
    platform_code: str
    event_date: date
    finishers: int
    previous_record: int
    previous_record_date: date | None = None


class PortalCourseRecordResponse(BaseModel):
    location_name: str
    platform_code: str
    event_date: date
    gender: str
    time_display: str
    runner_name: str | None = None
    previous_display: str | None = None
    previous_record_date: date | None = None
    delta_sec: int | None = None


class PortalWeekRecordsResponse(BaseModel):
    week_start: date
    week_end: date
    attendance: list[PortalAttendanceRecordResponse]
    course: list[PortalCourseRecordResponse]


class PortalTopSaturdayRowResponse(BaseModel):
    location_name: str
    platform_code: str
    finishers: int


class PortalYearPointResponse(BaseModel):
    year: int
    total: int
    platforms: dict[str, int]


class PortalYearProjectionResponse(BaseModel):
    year: int
    weeks_elapsed: int
    total: int
    platforms: dict[str, int]


class PortalWeekPointResponse(BaseModel):
    week_start: date
    total: int
    platforms: dict[str, int]


class PortalLocationsYearPointResponse(BaseModel):
    year: int
    platforms: dict[str, int]


class PortalLocationsWeekPointResponse(BaseModel):
    week_start: date
    platforms: dict[str, int]


class PortalAttendanceTopRowResponse(BaseModel):
    location_name: str
    platform_code: str
    event_date: date
    finishers: int


class PortalSystemCardResponse(BaseModel):
    code: str
    title: str
    locations: int
    finishes: int
    first_year: int | None = None
    last_year: int | None = None
    is_active: bool
    avg_finish_display: str | None = None


class PortalGeoPointResponse(BaseModel):
    latitude: float
    longitude: float
    starts: int
    location_name: str
    platform_code: str
    region: str | None = None


class PortalGeoResponse(BaseModel):
    locations_total: int
    regions_total: int
    points: list[PortalGeoPointResponse]


class PortalRecordFactResponse(BaseModel):
    location_name: str
    event_date: date
    value_display: str
    runner_name: str | None = None


class PortalBusiestDayResponse(BaseModel):
    event_date: date
    finishers: int


class PortalBusiestDayPlatformResponse(BaseModel):
    platform_code: str
    event_date: date
    finishers: int


class PortalFastestRowResponse(BaseModel):
    platform_code: str
    gender: str
    value_display: str
    runner_name: str | None = None
    location_name: str
    event_date: date
    delta_sec: int | None = None


class PortalGenderPlatformResponse(BaseModel):
    platform_code: str
    male: int
    female: int


class PortalGenderTotalResponse(BaseModel):
    male: int
    female: int


class PortalGenderSplitResponse(BaseModel):
    total: PortalGenderTotalResponse
    by_platform: list[PortalGenderPlatformResponse]


class PortalFunFactsResponse(BaseModel):
    total_distance_km: int
    total_time_sec: int
    avg_finish_display: str | None = None
    distance_delta_km: int | None = None
    time_delta_sec: int | None = None
    avg_delta_sec: int | None = None


class PortalHomeResponse(BaseModel):
    generated_at: datetime
    hero: PortalHeroResponse
    hero_last_year: PortalHeroResponse | None = None
    fastest: list[PortalFastestRowResponse]
    pulse: PortalPulseResponse | None = None
    week_records: PortalWeekRecordsResponse | None = None
    top_saturday: list[PortalTopSaturdayRowResponse]
    chart_years: list[PortalYearPointResponse]
    chart_year_projection: PortalYearProjectionResponse | None = None
    chart_weeks: list[PortalWeekPointResponse]
    locations_by_year: list[PortalLocationsYearPointResponse]
    locations_by_week: list[PortalLocationsWeekPointResponse]
    newcomers_by_year: list[PortalYearPointResponse]
    newcomers_by_week: list[PortalWeekPointResponse]
    newcomers_year_projection: PortalYearProjectionResponse | None = None
    attendance_year: int | None = None
    attendance_top_all: list[PortalAttendanceTopRowResponse]
    attendance_top_year: list[PortalAttendanceTopRowResponse]
    busiest_day_overall: PortalBusiestDayResponse | None = None
    busiest_day_by_platform: list[PortalBusiestDayPlatformResponse]
    systems: list[PortalSystemCardResponse]
    geo: PortalGeoResponse
    fun_facts: PortalFunFactsResponse
    gender_split: PortalGenderSplitResponse | None = None
