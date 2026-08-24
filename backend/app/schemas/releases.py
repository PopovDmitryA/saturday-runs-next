from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: str
    title: str
    body: str
    released_at: date


class ReleaseListResponse(BaseModel):
    """Страница истории релизов: срез `items` и его координаты в общем списке."""

    items: list[ReleaseResponse]
    total: int
    page: int = 1
    page_size: int = 0
    pages: int = 1
    latest_version: str | None = None


class ReleaseLatestResponse(BaseModel):
    version: str | None = None


class ReleaseAdminResponse(ReleaseResponse):
    is_published: bool
    created_at: datetime
    updated_at: datetime


class ReleaseAdminListResponse(BaseModel):
    items: list[ReleaseAdminResponse]
    total: int
    next_versions: dict[str, str]


class ReleaseCreateRequest(BaseModel):
    version: str
    title: str
    body: str
    released_at: date | None = None
    # Новый релиз по умолчанию скрыт: администратор открывает его сам.
    is_published: bool = False


class ReleaseUpdateRequest(ReleaseCreateRequest):
    pass
