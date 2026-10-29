from __future__ import annotations

from pydantic import BaseModel, Field


class AccountDeletionPreviewResponse(BaseModel):
    """Что исчезнет и что останется, если удалить профиль, — для окна подтверждения."""

    # Названия систем, профили которых привязаны («5 вёрст», «С95»…).
    platforms: list[str]
    # Способы входа («Telegram», «VK», «Яндекс», «Почта»).
    auth_providers: list[str]
    location_reviews: int
    review_photos: int
    goals: int
    run_tracks: int
    # Планы на старты и отметки «Не бегаю» (планировщик).
    run_plans: int
    # Постоянные планы («каждую субботу»), включая остановленные — их история.
    run_plan_series: int
    # Карточки и комментарии бэклога остаются без автора, голоса снимаются.
    backlog_cards: int
    backlog_comments: int
    backlog_votes: int
    # Ручные гранты кабинета организатора и автодоступ по волонтёрствам.
    organizer_grants: int
    is_organizer: bool
    has_avatar: bool


#: Случайный id попытки удаления, который придумывает браузер.
DELETION_ATTEMPT_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"


class AccountDeleteRequest(BaseModel):
    # Слово «удалить», набранное руками: случайный клик профиль не удалит.
    confirmation: str = Field(max_length=50)
    # По нему браузер потом спросит, удалила ли профиль именно эта попытка,
    # если ответ на неё не дошёл (GET /account/deletion-status).
    attempt_id: str | None = Field(default=None, pattern=DELETION_ATTEMPT_PATTERN)


class AccountDeletionStatusResponse(BaseModel):
    deleted: bool
