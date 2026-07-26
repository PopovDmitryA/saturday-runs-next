from __future__ import annotations

from pydantic import BaseModel, Field


class ChallengeLevelsResponse(BaseModel):
    bronze: int
    silver: int
    gold: int


class ChallengeLevelDatesResponse(BaseModel):
    """Дата достижения каждого уровня (ISO-дата или null, если не достигнут)."""

    bronze: str | None = None
    silver: str | None = None
    gold: str | None = None


class ChallengeResponse(BaseModel):
    code: str
    title: str
    icon: str
    description: str
    # collection | coincidence | scale | community
    category: str
    current: int
    target: int
    levels: ChallengeLevelsResponse
    # bronze | silver | gold | None (не достигнут)
    level: str | None = None
    next_level: str | None = None
    to_next_level: int | None = None
    # Человекочитаемое «сколько осталось» (p-индекс: «ещё 2 пробежки»)
    to_next_label: str | None = None
    pct: float = 0.0
    unit: str | None = None
    # Челлендж-специфичные детали: cells/letters/days/items — рисуются на фронте
    detail: dict[str, object] = Field(default_factory=dict)
    level_dates: ChallengeLevelDatesResponse
    # Насколько последняя пробежка продвинула счётчик (0 — не продвинула)
    recent_delta: int = 0


class BadgeResponse(BaseModel):
    code: str
    title: str
    icon: str
    level: str
    # Дата, когда был получен именно этот уровень
    achieved_at: str | None = None


class AchievementsSummaryResponse(BaseModel):
    gold: int = 0
    silver: int = 0
    bronze: int = 0
    total: int = 0


class ClubEntryResponse(BaseModel):
    # runs | volunteering
    code: str
    title: str
    icon: str
    current: int
    thresholds: list[int]
    earned: list[int]
    next_threshold: int | None = None
    to_next: int | None = None
    # Прогресс от предыдущего клуба к следующему
    pct_to_next: float = 0.0
    # Дата достижения каждого порога, ключ — строковое значение порога ("10", "25"…)
    level_dates: dict[str, str | None] = Field(default_factory=dict)


class ClubPlatformResponse(BaseModel):
    platform_code: str
    entries: list[ClubEntryResponse]


class ClubsResponse(BaseModel):
    # Уровень 1: сквозные клубы по всем системам
    overall: list[ClubEntryResponse]
    # Уровень 2: клубы внутри каждой системы
    platforms: list[ClubPlatformResponse]


class AchievementsResponse(BaseModel):
    challenges: list[ChallengeResponse]
    badges: list[BadgeResponse]
    summary: AchievementsSummaryResponse
    clubs: ClubsResponse


class GoalPresetResponse(BaseModel):
    goal_type: str
    title: str
    icon: str
    unit: str
    # count | time | streak | percent
    kind: str
    default_target: int
    min: int
    max: int
    description: str
    # Текущий показатель по этому пресету за год — не требует, чтобы цель была
    # выбрана: помогает адекватно выставить target при настройке.
    current_value: int = 0
    current_display: str | None = None


class GoalProgressResponse(BaseModel):
    goal_type: str
    year: int
    target_value: int
    title: str
    icon: str
    unit: str
    kind: str
    current_value: int
    pct: float
    done: bool
    on_track: bool | None = None
    forecast_value: int | None = None
    # Для целей на время: человекочитаемые значения (24:31)
    current_display: str | None = None
    target_display: str | None = None
    # Насколько последняя пробежка продвинула цель (0 — не продвинула;
    # для finish_under — насколько быстрее нового личного лучшего за год)
    recent_delta: int = 0


class GoalsResponse(BaseModel):
    year: int
    max_goals: int
    goals: list[GoalProgressResponse]
    presets: list[GoalPresetResponse]


class GoalInput(BaseModel):
    goal_type: str
    target_value: int


class GoalsUpdateRequest(BaseModel):
    goals: list[GoalInput] = Field(default_factory=list)


class StartNumberPlanEntryResponse(BaseModel):
    """Один предсказанный старт в ячейке таблицы планирования."""

    location: str
    location_slug: str
    platform_code: str
    date: str


class StartNumberPlanWeekResponse(BaseModel):
    index: int
    date_from: str
    date_to: str


class StartNumberPlanRowResponse(BaseModel):
    number: int
    # Закрыт ли номер участником (в любой системе)
    done: bool
    # По одному списку на недельное окно, в порядке weeks
    weeks: list[list[StartNumberPlanEntryResponse]]


class StartNumberPlanResponse(BaseModel):
    code: str
    low: int
    high: int
    generated_for: str
    weeks: list[StartNumberPlanWeekResponse]
    rows: list[StartNumberPlanRowResponse]
