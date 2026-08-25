from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class ProfilePreviewActivityResponse(BaseModel):
    kind: str
    event_date: date
    location_name: str
    finish_time_display: str | None = None
    role: str | None = None


class ProfileUrlRequest(BaseModel):
    # Короткие штрихкоды parkrun (например A7035519) тоже допустимы.
    profile_url: str = Field(min_length=2, max_length=1024)


class ProfilePreviewResponse(BaseModel):
    platform_code: str
    external_user_id: str
    display_name: str
    profile_url: str
    total_runs: int | None = None
    total_volunteering: int | None = None
    club_name: str | None = None
    barcode_id: str | None = None
    planning_location: str | None = None
    planning_location_seen_at: object | None = None
    age_category: str | None = None
    parkrun_eligible: bool = False
    recent_activities: list[ProfilePreviewActivityResponse] = Field(default_factory=list)
    data_source: str = "live"
    data_updated_at: object | None = None
    data_through_date: date | None = None


class S95ProfilePreviewResponse(ProfilePreviewResponse):
    parkrun_match: ProfilePreviewResponse | None = None


class S95ConfirmRequest(BaseModel):
    profile_url: str = Field(min_length=2, max_length=1024)
    link_parkrun: bool = False


class PlatformLinkResponse(BaseModel):
    id: UUID
    platform_code: str
    platform_name: str
    external_user_id: str
    external_url: str
    display_name: str | None = None
    barcode_id: str | None = None
    sync_status: str
    last_user_sync_at: object | None = None
    data_updated_at: object | None = None
    data_through_date: date | None = None

    model_config = {"from_attributes": True}


class ProfileClaimRequest(BaseModel):
    """Привязка по ID из тизера главной: система + номер участника."""

    platform_code: str = Field(min_length=1, max_length=32)
    athlete_id: str = Field(min_length=1, max_length=32)


class ProfileClaimResponse(BaseModel):
    # "linked" — привязали сейчас, "already_linked" — профиль этой системы у
    # человека уже был (для сквозного пути это успех, а не ошибка).
    status: str
    platform_code: str
    link: PlatformLinkResponse | None = None


class ProfileLinkConfirmResponse(BaseModel):
    link: PlatformLinkResponse
    message: str = "linked"


class S95ProfileLinkConfirmResponse(BaseModel):
    link: PlatformLinkResponse
    parkrun_link: PlatformLinkResponse | None = None
    message: str = "linked"


class ProfileUnlinkResponse(BaseModel):
    platform_code: str
    message: str = "unlinked"
    cancelled_sync_jobs: int = 0
