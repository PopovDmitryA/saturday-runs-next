from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

Score = Annotated[int, Field(ge=1, le=5)]
OptScore = Annotated[int | None, Field(ge=1, le=5)]


class RatingUpsertRequest(BaseModel):
    """Оценка одного старта. Обязательна только общая оценка."""

    score_overall: Score
    score_organization: OptScore = None
    score_route: OptScore = None
    score_community: OptScore = None
    comment: str | None = Field(default=None, max_length=2000)
    is_public: bool = False

    @model_validator(mode="after")
    def _require_comment_for_low_score(self) -> RatingUpsertRequest:
        if self.comment is not None:
            self.comment = self.comment.strip() or None
        # При низкой оценке (≤2) в ЛЮБОМ из критериев просим пояснить комментарием.
        has_low_score = self.score_overall <= 2 or any(
            score is not None and score <= 2
            for score in (self.score_organization, self.score_route, self.score_community)
        )
        if has_low_score and not self.comment:
            raise ValueError("Для оценки 2★ и ниже добавьте комментарий")
        return self


class RatingResponse(BaseModel):
    # Опаковый id старта: 'run:<uuid>' (бегун) / 'vol:<uuid>' (волонтёр).
    entry_id: str
    participation_type: str = "run"
    run_result_id: UUID | None = None
    score_overall: int
    score_organization: int | None = None
    score_route: int | None = None
    score_community: int | None = None
    comment: str | None = None
    is_public: bool = False
    # можно ли ещё исправить/удалить (в пределах 3 месяцев) или уже зафиксировано
    editable: bool = True
    created_at: datetime
    updated_at: datetime


class MyRatingItem(RatingResponse):
    event_date: date
    platform_code: str
    location_name: str
    location_city: str | None = None
    finish_time_display: str | None = None
    position: int | None = None
    is_pr: bool = False
    event_url: str | None = None


class MyRatingsResponse(BaseModel):
    can_rate: bool
    total_runs: int
    min_runs_required: int
    create_window_days: int
    edit_window_days: int
    ratings: list[MyRatingItem] = Field(default_factory=list)


class AdminRatingRow(BaseModel):
    id: UUID
    user_id: UUID
    user_display: str
    user_serial: int | None = None
    event_date: date
    platform_code: str
    location_key: str
    location_name: str
    location_city: str | None = None
    score_overall: int
    score_organization: int | None = None
    score_route: int | None = None
    score_community: int | None = None
    comment: str | None = None
    is_public: bool = False
    editable: bool = True
    participation_type: str = "run"
    created_at: datetime


class AdminRatingsStatGroup(BaseModel):
    last_1d: int = 0
    last_7d: int = 0
    last_30d: int = 0
    total: int = 0


class AdminRatingsStats(BaseModel):
    by_rating_date: AdminRatingsStatGroup = Field(default_factory=AdminRatingsStatGroup)
    by_event_date: AdminRatingsStatGroup = Field(default_factory=AdminRatingsStatGroup)


class AdminRatingsResponse(BaseModel):
    ratings: list[AdminRatingRow] = Field(default_factory=list)
    stats: AdminRatingsStats = Field(default_factory=AdminRatingsStats)


class AdminLocationRatingRow(BaseModel):
    location_key: str
    location_name: str
    voters: int
    ratings: int
    avg_overall: float | None = None
    avg_organization: float | None = None
    avg_route: float | None = None
    avg_community: float | None = None
    meets_threshold: bool = False


class AdminLocationRatingsResponse(BaseModel):
    excluded_locals: bool
    min_voters: int
    locations: list[AdminLocationRatingRow] = Field(default_factory=list)


class EligibleRunResponse(BaseModel):
    entry_id: str
    participation_type: str = "run"
    run_result_id: UUID | None = None
    event_date: date
    platform_code: str
    location_name: str
    location_city: str | None = None
    # Канонический ключ площадки (catalog:<uuid> / location:<uuid>) — тот же,
    # что identity_key страницы локации: slug у разных платформ свой, а ключ один.
    location_identity_key: str | None = None
    finish_time_display: str | None = None
    position: int | None = None
    is_pr: bool = False
    volunteer_role: str | None = None
    event_url: str | None = None
    # Старт из добора истории: он вне окна создания и доступен как единственный
    # шанс оценить эту локацию.
    is_legacy: bool = False
    my_rating: RatingResponse | None = None


class RatingEligibilityResponse(BaseModel):
    can_rate: bool
    total_runs: int
    min_runs_required: int
    window_days: int
    runs: list[EligibleRunResponse] = Field(default_factory=list)
