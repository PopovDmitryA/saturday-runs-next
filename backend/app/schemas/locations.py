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


class CatalogLocationTableRowResponse(BaseModel):
    row_key: str
    catalog_identity_key: str
    location_id: str
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
