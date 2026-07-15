from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TopLocationResponse(BaseModel):
    name: str
    platform_codes: list[str]
    count: int
    tied_count: int = 1


class TopVolunteerRoleResponse(BaseModel):
    role: str
    count: int


class PlatformRunMetricsResponse(BaseModel):
    platform_code: str
    avg_finish_time_sec: int | None = None
    avg_pace_sec_per_km: int | None = None


class MonthlyActivityResponse(BaseModel):
    month: str
    runs: int = 0
    volunteering: int = 0


class MonthlyPaceResponse(BaseModel):
    month: str
    avg_pace_sec_per_km: int | None = None


class ActivityCalendarItemResponse(BaseModel):
    platform_code: str
    location: str


class ActivityCalendarDayResponse(BaseModel):
    date: date
    runs: int = 0
    volunteering: int = 0
    run_items: list[ActivityCalendarItemResponse] = Field(default_factory=list)
    volunteer_items: list[ActivityCalendarItemResponse] = Field(default_factory=list)


class DashboardAnalyticsResponse(BaseModel):
    analytics_version: int = 1
    unique_locations: int = 0
    unique_run_locations: int = 0
    unique_run_regions: int = 0
    unique_run_cities: int = 0
    unique_volunteer_locations: int = 0
    unique_volunteer_regions: int = 0
    unique_volunteer_cities: int = 0
    avg_finish_time_sec: int | None = None
    best_finish_time_sec: int | None = None
    best_results_platform_count: int = 0
    avg_pace_sec_per_km: int | None = None
    avg_position: float | None = None
    avg_gender_position: float | None = None
    pr_count: int = 0
    unique_volunteer_roles: int = 0
    first_activity_date: date | None = None
    last_activity_date: date | None = None
    first_run_date: date | None = None
    days_since_first_run: int | None = None
    top_location: TopLocationResponse | None = None
    top_volunteer_role: TopVolunteerRoleResponse | None = None
    runs_last_12_months: int = 0
    runs_current_year: int = 0
    volunteering_last_12_months: int = 0
    volunteering_current_year: int = 0
    volunteering_index: str | None = None
    saturday_streak: int = 0
    saturday_streak_max: int = 0
    saturday_run_streak_max: int = 0
    saturday_vol_streak_max: int = 0
    saturday_streak_current: int = 0
    saturday_run_streak_current: int = 0
    saturday_vol_streak_current: int = 0
    activity_calendar: list[ActivityCalendarDayResponse] = Field(default_factory=list)
    finish_times_sec: list[int] = Field(default_factory=list)
    saturday_consistency_pct: float | None = None
    saturday_consistency_active: int = 0
    saturday_consistency_total: int = 0
    total_distance_km: int = 0
    next_milestone_runs: int | None = None
    runs_to_next_milestone: int | None = None
    last_pr_date: date | None = None
    last_global_pr_date: date | None = None
    pr_last_12_months: int = 0
    new_locations_last_12_months: int = 0
    run_clubs_earned: list[int] = Field(default_factory=list)
    next_run_club: int | None = None
    avg_vs_field_pct: float | None = None
    runs_with_field_avg_count: int = 0
    platform_metrics: list[PlatformRunMetricsResponse] = Field(default_factory=list)
    activity_by_month: list[MonthlyActivityResponse] = Field(default_factory=list)
    pace_trend: list[MonthlyPaceResponse] = Field(default_factory=list)


class OnThisDayRunResponse(BaseModel):
    years_ago: int
    event_date: date
    location_name: str
    location_city: str | None = None
    platform_code: str
    finish_time_display: str | None = None
    finish_time_sec: int | None = None
    position: int | None = None
    is_pr: bool = False
    event_url: str | None = None


class OnThisDayResponse(BaseModel):
    kind: str | None = None
    run: OnThisDayRunResponse | None = None
    runs: list[OnThisDayRunResponse] = Field(default_factory=list)
    also_count: int = 0
    today_iso: str


class MyHistoryMilestoneResponse(BaseModel):
    # Вид вехи — см. app.history_milestone_kinds.MILESTONE_KIND_REGISTRY.
    kind: str
    # Номер пробежки/клуба/волонтёрства либо порядковый номер региона/города/страны.
    number: int | None = None
    event_date: date
    platform_code: str
    location_name: str
    location_city: str | None = None
    finish_time_display: str | None = None
    finish_time_sec: int | None = None
    position: int | None = None
    gender_position: int | None = None
    pace_display: str | None = None
    # На сколько секунд улучшен личный рекорд (для kind=pr).
    delta_sec: int | None = None
    is_global_pr: bool = False
    region: str | None = None
    country: str | None = None
    # Волонтёрская роль (для волонтёрских вех).
    role: str | None = None
    event_url: str | None = None


class MyHistoryResponse(BaseModel):
    milestones: list[MyHistoryMilestoneResponse] = Field(default_factory=list)
    total: int = 0


class DashboardStatsResponse(BaseModel):
    total_runs: int = 0
    total_volunteering: int = 0
    by_platform: dict[str, dict[str, int]] = Field(default_factory=dict)
    analytics: DashboardAnalyticsResponse = Field(default_factory=DashboardAnalyticsResponse)


class DashboardLinkSummary(BaseModel):
    platform_code: str
    external_user_id: str
    sync_status: str
    last_user_sync_at: object | None = None


class DashboardResponse(BaseModel):
    stats: DashboardStatsResponse
    computed_at: object
    platform_links: list[DashboardLinkSummary] = Field(default_factory=list)
    sync_enqueued: bool = False
    serial_id: int | None = None
    # Уникальная vanity-ссылка на профиль; если задана — используется вместо serial_id.
    public_slug: str | None = None


class RunItemResponse(BaseModel):
    run_result_id: UUID | None = None
    platform_code: str
    event_date: date
    event_number: int | None = None
    location_name: str
    location_source_name: str | None = None
    location_city: str | None = None
    location_country: str | None = None
    location_is_paused: bool = False
    location_is_cancelled: bool = False
    position: int | None = None
    gender_position: int | None = None
    finish_time_display: str | None = None
    finish_time_sec: int | None = None
    pace_display: str | None = None
    pace_sec_per_km: int | None = None
    age_category: str | None = None
    is_pr: bool = False
    is_global_pr: bool = False
    is_location_pr: bool = False
    is_crosslinked: bool = False
    is_first_run: bool = False
    is_first_run_at_location: bool = False
    club_name: str | None = None
    achievement_labels: list[str] = Field(default_factory=list)
    status: str | None = None
    is_test_event: bool = False
    event_url: str | None = None


class BestResultResponse(BaseModel):
    platform_code: str
    event_date: date
    location_name: str
    location_city: str | None = None
    finish_time_display: str | None = None
    finish_time_sec: int | None = None
    event_url: str | None = None


class PersonalRecordResponse(BaseModel):
    platform_code: str
    event_date: date
    location_name: str
    location_city: str | None = None
    finish_time_display: str | None = None
    finish_time_sec: int | None = None
    is_global_pr: bool = False
    event_url: str | None = None


class CoRunnerResponse(BaseModel):
    participant_key: str
    display_name: str | None = None
    profile_url: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    site_serial_id: int | None = None
    meetings: int = 0
    my_wins: int = 0
    their_wins: int = 0
    timed_meetings: int = 0
    first_meeting_date: date | None = None
    last_meeting_date: date | None = None


class CoRunnerMeetingResponse(BaseModel):
    event_date: date
    platform_code: str
    location_name: str
    my_time_sec: int | None = None
    their_time_sec: int | None = None
    my_position: int | None = None
    their_position: int | None = None
    event_url: str | None = None


class VolunteerRoleStatResponse(BaseModel):
    platform_code: str
    role: str
    count: int


class VolunteeringItemResponse(BaseModel):
    platform_code: str
    event_date: date
    event_number: int | None = None
    location_name: str
    location_source_name: str | None = None
    location_city: str | None = None
    location_country: str | None = None
    location_is_paused: bool = False
    location_is_cancelled: bool = False
    role: str | None = None
    volunteer_result_id: UUID | None = None
    # Опаковый id старта для оценки (общий на все роли одного волонтёрства).
    rating_entry_id: str | None = None
    is_crosslinked: bool = False
    is_test_event: bool = False
    event_url: str | None = None


class PlatformLinkSyncStatusResponse(BaseModel):
    platform_code: str
    sync_status: str
    last_user_sync_at: object | None = None
    error_message: str | None = None
    error_details: str | None = None


class SyncJobSummaryResponse(BaseModel):
    id: UUID
    status: str
    trigger: str
    started_at: object | None = None
    finished_at: object | None = None
    error_message: str | None = None
    error_details: str | None = None
    created_at: object


class SyncStatusResponse(BaseModel):
    platform_links: list[PlatformLinkSyncStatusResponse] = Field(default_factory=list)
    latest_job: SyncJobSummaryResponse | None = None
    dashboard_cache_computed_at: object | None = None


class SyncRefreshResponse(BaseModel):
    job_id: UUID
    status: str
    message: str = (
        "Запрос на обновление отправлен. Ожидайте исполнения в ближайшее время."
    )


class SyncQueueTaskResponse(BaseModel):
    celery_task_id: str
    suffix: str
    queue: str
    celery_state: str
    queue_position: int | None = None
    queue_length: int = 0


class SyncQueueJobUserResponse(BaseModel):
    telegram_id: int | None = None
    telegram_username: str | None = None
    display_name: str | None = None


class SyncQueueJobResponse(BaseModel):
    id: UUID
    trigger: str
    status: str
    platform_code: str | None = None
    created_at: object
    started_at: object | None = None
    finished_at: object | None = None
    error_message: str | None = None
    error_details: str | None = None
    tasks: list[SyncQueueTaskResponse] = Field(default_factory=list)
    user: SyncQueueJobUserResponse | None = None


class SyncQueueSummaryResponse(BaseModel):
    queue: str
    label: str
    length: int


class SyncQueuePipelineTaskResponse(BaseModel):
    label: str
    started_at: datetime | None = None
    source: str | None = None
    finished_at: datetime | None = None


class SyncQueuePipelineResponse(BaseModel):
    running: list[SyncQueuePipelineTaskResponse] = Field(default_factory=list)
    last_success: list[SyncQueuePipelineTaskResponse] = Field(default_factory=list)
    queue_depths: dict[str, int] = Field(default_factory=dict)
    checked_at: datetime | None = None


class SyncQueueParkrunQueueResponse(BaseModel):
    pending: int = 0
    failed: int = 0
    stuck_done: int = 0
    processing: int = 0
    celery_sync: int = 0
    captcha_pending: bool = False
    cooldown_remaining_seconds: int | None = None
    worker_alive: bool = False
    worker_status: str = "idle"
    s95_pending: int = 0
    s95_failed: int = 0
    s95_processing: int = 0


class SyncQueueResponse(BaseModel):
    jobs: list[SyncQueueJobResponse] = Field(default_factory=list)
    queues: list[SyncQueueSummaryResponse] = Field(default_factory=list)
    active_jobs_count: int = 0
    pipeline: SyncQueuePipelineResponse | None = None
    parkrun_queue: SyncQueueParkrunQueueResponse | None = None
