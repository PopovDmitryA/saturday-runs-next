from __future__ import annotations

from pydantic import BaseModel


class LeaderboardCellResponse(BaseModel):
    value: int
    delta: int


class LeaderboardRowResponse(BaseModel):
    rank: int
    rank_delta: int
    display_name: str | None
    site_serial_id: int | None
    platforms: dict[str, LeaderboardCellResponse]
    total: int
    total_delta: int
    # Только у метрики wins: «домашняя трибуна» — локация с максимумом побед.
    home_location: str | None = None
    home_location_wins: int | None = None


class LeaderboardResponse(BaseModel):
    metric: str
    title: str
    description: str
    unit: str
    platform_columns: list[str]
    rows: list[LeaderboardRowResponse]
    threshold: int
    median: int
    entrants: int
    latest_event_date: str | None
    week_start: str | None
    built_at: str | None


class MyLeaderboardRowResponse(BaseModel):
    metric: str
    display_name: str | None
    site_serial_id: int
    platforms: dict[str, LeaderboardCellResponse]
    total: int
    total_delta: int
    rank: int | None
    rank_delta: int | None
    included: bool
    threshold: int
    home_location: str | None = None
    home_location_wins: int | None = None
