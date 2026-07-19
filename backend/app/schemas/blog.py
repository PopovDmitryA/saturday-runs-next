from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BlogPostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    teaser: str
    telegram_url: str
    topic: str | None = None
    published_at: datetime
    clicks_count: int


class BlogTopicResponse(BaseModel):
    topic: str
    posts: int


class BlogPostListResponse(BaseModel):
    items: list[BlogPostResponse]
    total: int
    topics: list[BlogTopicResponse]


class BlogHomeResponse(BaseModel):
    items: list[BlogPostResponse]


class BlogClickResponse(BaseModel):
    clicks_count: int


class BlogPostAdminResponse(BlogPostResponse):
    is_published: bool
    created_at: datetime
    updated_at: datetime


class BlogPostAdminListResponse(BaseModel):
    items: list[BlogPostAdminResponse]
    total: int


class BlogPostCreateRequest(BaseModel):
    title: str
    teaser: str
    telegram_url: str
    topic: str | None = None
    published_at: datetime | None = None
    is_published: bool = True


class BlogPostUpdateRequest(BlogPostCreateRequest):
    pass
