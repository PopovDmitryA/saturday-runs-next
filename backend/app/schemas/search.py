"""Схемы публичного поиска по сайту и журнала поисковых запросов."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SearchLocationResponse(BaseModel):
    slug: str
    name: str
    city: str | None = None
    platform_codes: list[str] = Field(default_factory=list)
    href: str


class SearchRegisteredPersonResponse(BaseModel):
    """Человек с открытым профилем на сайте — ведёт на наш /users/{хендл}."""

    kind: Literal["registered"] = "registered"
    display_name: str
    href: str
    avatar_url: str | None = None
    total_runs: int = 0
    total_volunteering: int = 0
    top_location_name: str | None = None
    platform_codes: list[str] = Field(default_factory=list)


class SearchParticipantResponse(BaseModel):
    """Участник без открытого профиля на сайте: ни ссылки, ни внешнего адреса.

    Поля profile_url здесь нет намеренно — на чужие профили в системах мы не
    ссылаемся (согласия нет), а схема не даст ему просочиться случайно.
    """

    kind: Literal["participant"] = "participant"
    display_name: str
    total_runs: int = 0
    total_volunteering: int = 0
    top_location_name: str | None = None
    top_location_city: str | None = None
    platform_codes: list[str] = Field(default_factory=list)


SearchPersonResponse = Annotated[
    SearchRegisteredPersonResponse | SearchParticipantResponse,
    Field(discriminator="kind"),
]


class SiteSearchResponse(BaseModel):
    query: str
    # Запрос в другой раскладке, по которому и нашлось показанное; None — не переводили.
    corrected_query: str | None = None
    locations: list[SearchLocationResponse] = Field(default_factory=list)
    people: list[SearchPersonResponse] = Field(default_factory=list)
    people_truncated: bool = False


class SearchLogTopQuery(BaseModel):
    query: str
    count: int
    zero_results_count: int
    clicks: int


class SearchLogZeroQuery(BaseModel):
    query: str
    count: int
    last_at: dt.datetime


class SearchLogClicksByKind(BaseModel):
    page: int = 0
    location: int = 0
    person: int = 0
    none: int = 0


class SearchLogDaily(BaseModel):
    # Сутки по Москве.
    date: dt.date
    count: int


class SearchLogRecentItem(BaseModel):
    created_at: dt.datetime
    query: str
    corrected_query: str | None = None
    pages_found: int
    locations_found: int
    people_found: int
    clicked_kind: str | None = None
    clicked_target: str | None = None
    is_authed: bool
    is_mobile: bool


class AdminSearchLogResponse(BaseModel):
    period_days: int
    total: int
    zero_result_total: int
    top_queries: list[SearchLogTopQuery] = Field(default_factory=list)
    zero_result_queries: list[SearchLogZeroQuery] = Field(default_factory=list)
    clicks_by_kind: SearchLogClicksByKind
    daily: list[SearchLogDaily] = Field(default_factory=list)
    recent: list[SearchLogRecentItem] = Field(default_factory=list)
