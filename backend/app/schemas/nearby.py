from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NearbyWhiteSpot(BaseModel):
    cell_latitude: float
    cell_longitude: float
    place_label: str | None = None
    count: int
    nearest_name: str | None = None
    nearest_slug: str | None = None
    nearest_distance_km: float | None = None
    last_at: datetime


class NearbyTopLocation(BaseModel):
    nearest_name: str | None = None
    nearest_slug: str | None = None
    count: int


class NearbyDaily(BaseModel):
    day: str
    count: int
    nothing_near: int


class AdminNearbyLogResponse(BaseModel):
    period_days: int
    radius_km: float
    white_spot_km: float
    total: int
    nothing_near_total: int
    linked_total: int
    inline_total: int
    white_spots: list[NearbyWhiteSpot] = Field(default_factory=list)
    top_nearest: list[NearbyTopLocation] = Field(default_factory=list)
    daily: list[NearbyDaily] = Field(default_factory=list)
