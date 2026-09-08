from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AdminResyncCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class AdminResyncStep(BaseModel):
    at: datetime
    code: str
    text: str
    details: dict[str, Any] | None = None


class AdminResyncRequestOut(BaseModel):
    id: UUID
    platform_code: str
    kind: str
    input_url: str
    target: dict[str, Any]
    status: str
    steps: list[AdminResyncStep]
    result: dict[str, Any] | None = None
    error_message: str | None = None
    summary: str = ""
    queue_position: int | None = None
    queue_length: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AdminResyncListResponse(BaseModel):
    items: list[AdminResyncRequestOut]
