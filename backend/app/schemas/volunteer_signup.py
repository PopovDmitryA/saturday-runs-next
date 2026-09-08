"""Схемы заявок на волонтёрство: страница локации и кабинет организатора."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SignupLocationBrief(BaseModel):
    slug: str
    name: str


class SignupDateOption(BaseModel):
    date: date
    date_display: str


class SignupRoleOption(BaseModel):
    name: str
    # Сколько строк (мест) у роли в открытой записи; 1 — по умолчанию.
    slots: int = 1
    # Дата (dd.mm.yyyy) → кто уже записан по открытой записи 5verst.ru.
    filled: dict[str, str] = Field(default_factory=dict)


class SignupRequestItem(BaseModel):
    id: UUID
    event_date: date
    event_date_display: str
    role_name: str
    verst_id: str
    participant_name: str | None = None
    comment: str | None = None
    # pending | confirmed | declined | cancelled
    status: str
    created_at: datetime
    decided_at: datetime | None = None
    decision_note: str | None = None
    # none | saved | failed | manual
    nrms_status: str
    nrms_error: str | None = None
    organizer_notified: bool = False
    # None — открытая запись недоступна или дата за её горизонтом.
    in_open_roster: bool | None = None


class SignupOptionsResponse(BaseModel):
    location: SignupLocationBrief
    # False — у локации нет половины 5 вёрст: ни NRMS, ни открытой записи.
    supported: bool
    five_verst_slug: str | None = None
    roster_url: str | None = None
    roster_available: bool = False
    # Есть ли у локации организатор с доступом к кабинету — иначе заявке некуда идти.
    organizer_connected: bool = False
    linked: bool = False
    verst_id: str | None = None
    participant_name: str | None = None
    dates: list[SignupDateOption] = Field(default_factory=list)
    roles: list[SignupRoleOption] = Field(default_factory=list)
    my_requests: list[SignupRequestItem] = Field(default_factory=list)
    # Готовый текст для чата локации, когда организатор к сайту не подключён.
    fallback_message: str


class SignupCreateRequest(BaseModel):
    event_date: date
    role_name: str = Field(min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=500)


class SignupCreateResponse(BaseModel):
    item: SignupRequestItem


class OrganizerSignupListResponse(BaseModel):
    location: SignupLocationBrief
    supported: bool
    five_verst_slug: str | None = None
    roster_url: str | None = None
    roster_available: bool = False
    pending_count: int = 0
    items: list[SignupRequestItem] = Field(default_factory=list)


class SignupDecisionRequest(BaseModel):
    # confirmed | declined
    decision: str = Field(pattern="^(confirmed|declined)$")
    note: str | None = Field(default=None, max_length=500)


class SignupDecisionResponse(BaseModel):
    item: SignupRequestItem


class SignupBulkDecisionItem(BaseModel):
    request_id: UUID
    decision: str = Field(pattern="^(confirmed|declined)$")
    note: str | None = Field(default=None, max_length=500)


class SignupBulkDecisionRequest(BaseModel):
    decisions: list[SignupBulkDecisionItem] = Field(min_length=1, max_length=100)


class SignupBulkDecisionResponse(BaseModel):
    items: list[SignupRequestItem] = Field(default_factory=list)


class NrmsSessionState(BaseModel):
    connected: bool
    username: str | None = None
    expires_at: datetime | None = None


class NrmsEventItem(BaseModel):
    id: int
    name: str
    url: str


class NrmsLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NrmsLoginResponse(NrmsSessionState):
    events: list[NrmsEventItem] = Field(default_factory=list)
