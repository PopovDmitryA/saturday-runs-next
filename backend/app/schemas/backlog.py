from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import BacklogCardStatus, BacklogCardType
from app.schemas.photo import PhotoResponse


class BacklogCardCreateRequest(BaseModel):
    type: BacklogCardType
    category: str = Field(max_length=64)
    title: str = Field(max_length=200)
    description: str
    is_anonymous: bool = False


class BacklogCardUpdateRequest(BaseModel):
    """Частичное редактирование карточки. Все поля опциональны — приходят только
    изменённые. Правит автор (свою) или администратор (любую). Поле status
    применяет только администратор; у автора оно молча недоступно (см.
    backlog_service.update_card)."""

    type: BacklogCardType | None = None
    category: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    is_anonymous: bool | None = None
    status: BacklogCardStatus | None = None


class BacklogVoteRequest(BaseModel):
    # 0 снимает голос (повторный клик по уже выбранной кнопке).
    value: int = Field(ge=-1, le=1)


class BacklogCommentCreateRequest(BaseModel):
    body: str
    is_anonymous: bool = False


class BacklogCardResponse(BaseModel):
    id: UUID
    type: BacklogCardType
    category: str
    title: str
    description: str
    status: BacklogCardStatus
    is_anonymous: bool
    author_display_name: str | None = None
    author_handle: str | None = None
    # Аватар автора, если загружен и карточка не анонимная.
    author_avatar_url: str | None = None
    # Оригинал аватара — раскрывается по клику на неё.
    author_avatar_full_url: str | None = None
    upvotes: int
    downvotes: int
    score: int
    my_vote: int
    comment_count: int
    # Когда карточку перевели в «реализовано» — лента показывает «реализовано N назад».
    done_at: datetime | None = None
    # Своя ли карточка для текущего зрителя. Считается на бэкенде (фронт не знает
    # author_user_id) — по нему рисуется кнопка «Редактировать». Работает и для
    # своих анонимных карточек, где автор скрыт от остальных.
    is_mine: bool = False
    photos: list[PhotoResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class BacklogCardListResponse(BaseModel):
    items: list[BacklogCardResponse]
    total: int


class BacklogCommentResponse(BaseModel):
    id: UUID
    card_id: UUID
    body: str
    is_anonymous: bool
    author_display_name: str | None = None
    author_handle: str | None = None
    author_avatar_url: str | None = None
    # Ответ администратора/разработчика сайта — помечается на фронте бейджем
    # (админу анонимность недоступна, см. _resolve_anonymous в backlog_service).
    is_admin_author: bool = False
    # Своя ли это реплика для текущего зрителя. Считается на бэкенде, чтобы
    # чат мог выровнять свои сообщения вправо, не раскрывая автора анонимных.
    is_mine: bool = False
    created_at: datetime


class BacklogCommentListResponse(BaseModel):
    items: list[BacklogCommentResponse]


class BacklogCategoryResponse(BaseModel):
    key: str
    label: str


class BacklogCategoryListResponse(BaseModel):
    items: list[BacklogCategoryResponse]


class BacklogCardAdminResponse(BaseModel):
    id: UUID
    type: BacklogCardType
    category: str
    title: str
    description: str
    status: BacklogCardStatus
    is_anonymous: bool
    author_display_name: str | None = None
    author_handle: str | None = None
    # Аватар автора, если загружен и карточка не анонимная.
    author_avatar_url: str | None = None
    # Оригинал аватара — раскрывается по клику на неё.
    author_avatar_full_url: str | None = None
    upvotes: int
    downvotes: int
    score: int
    comment_count: int
    created_at: datetime
    updated_at: datetime


class BacklogCardAdminListResponse(BaseModel):
    items: list[BacklogCardAdminResponse]
    total: int


class BacklogCardStatusUpdateRequest(BaseModel):
    status: BacklogCardStatus


class BacklogVoteAdminItem(BaseModel):
    # Голосование не анонимно — админ видит реального автора каждого голоса.
    value: int
    author_display_name: str
    author_handle: str
    created_at: datetime


class BacklogVoteAdminListResponse(BaseModel):
    upvotes: list[BacklogVoteAdminItem]
    downvotes: list[BacklogVoteAdminItem]
