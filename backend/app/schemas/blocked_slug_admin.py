from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BlockedSlugItem(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    slug: str
    comment: str | None = None
    created_at: datetime


class BlockedSlugListResponse(BaseModel):
    items: list[BlockedSlugItem] = Field(default_factory=list)


class BlockedSlugCreateRequest(BaseModel):
    slug: str
    comment: str | None = None
