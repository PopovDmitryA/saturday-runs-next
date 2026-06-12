from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class RunCountRatingRowResponse(BaseModel):
    rank: int
    runner_name: str
    run_count: int
    ran_on_latest_date: bool
    latest_location: str | None = None
    skipped_above_count: int = Field(description="Кол-во пропустивших на последней дате (выше или на той же позиции)")


class RunCountRatingResponse(BaseModel):
    title: str
    description: str
    platform_code: str = "five_verst"
    data_source: str
    latest_event_date: date | None = None
    data_updated_at: datetime | None = None
    cached_at: datetime | None = None
    rows: list[RunCountRatingRowResponse]
