from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

# Публичные схемы — белый список: id статей и переводов, имена анонимных
# переводов и даты записи наружу не уходят (сторож — tests/test_site_funding.py).


class FundingCostOut(BaseModel):
    title: str
    hint: str | None = None
    amount_rub: int


class FundingDonationOut(BaseModel):
    received_on: date
    # None — «Аноним»: перевод отмечен анонимным или пришёл без имени.
    name: str | None = None
    amount_rub: int


class FundingMonthResponse(BaseModel):
    visible: bool
    month: str | None = None
    current_month: str | None = None
    first_month: str | None = None
    # Последний месяц со стрелкой «вперёд»: текущий или будущий с планом расходов.
    last_month: str | None = None
    costs: list[FundingCostOut] = Field(default_factory=list)
    cost_total_rub: int = 0
    donations: list[FundingDonationOut] = Field(default_factory=list)
    donated_total_rub: int = 0


# ---------- админка ----------


class FundingItemOut(BaseModel):
    id: UUID
    title: str
    hint: str | None = None


class FundingCostLineOut(BaseModel):
    item_id: UUID
    title: str
    hint: str | None = None
    amount_rub: int


class FundingAdminDonationOut(BaseModel):
    id: UUID
    received_on: date
    amount_rub: int
    donor_name: str | None = None
    is_anonymous: bool


class FundingDonorNameOut(BaseModel):
    name: str
    count: int


class FundingAdminMonthResponse(BaseModel):
    month: str
    current_month: str
    max_month: str
    show_public: bool
    items: list[FundingItemOut]
    costs: list[FundingCostLineOut]
    # Месяц, из которого скопированы суммы, если у этого месяца своих нет.
    costs_copied_from: str | None = None
    cost_total_rub: int
    donations: list[FundingAdminDonationOut]
    donated_total_rub: int
    donor_names: list[FundingDonorNameOut]


class FundingSettingsRequest(BaseModel):
    show_public: bool


class FundingSettingsResponse(BaseModel):
    show_public: bool


class FundingCostAmountIn(BaseModel):
    item_id: UUID
    amount_rub: int


class FundingMonthCostsRequest(BaseModel):
    items: list[FundingCostAmountIn]


class FundingItemRequest(BaseModel):
    title: str
    hint: str | None = None


class FundingDonationRequest(BaseModel):
    received_on: date
    amount_rub: int
    donor_name: str | None = None
    is_anonymous: bool = False
