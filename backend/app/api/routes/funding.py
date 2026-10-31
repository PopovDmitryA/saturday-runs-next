from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.db.session import get_db
from app.models import SiteDonation, User
from app.schemas.abuse_admin import AbuseMessageResponse
from app.schemas.funding import (
    FundingAdminDonationOut,
    FundingAdminMonthResponse,
    FundingCostLineOut,
    FundingCostOut,
    FundingDonationOut,
    FundingDonationRequest,
    FundingDonorNameOut,
    FundingItemOut,
    FundingItemRequest,
    FundingMonthCostsRequest,
    FundingMonthResponse,
    FundingSettingsRequest,
    FundingSettingsResponse,
)
from app.services import site_funding_service as funding

# Донаты: публичный месяц для блока «Поддержать проект» (/about, /backlog) и
# админка /admin/donations. Правила — в докстринге site_funding_service.
router = APIRouter(tags=["funding"])

# Свой ввод в админке админ увидит на сайте почти сразу, а повторные заходы
# на /about не гоняют запрос.
_PUBLIC_CACHE_CONTROL = "public, max-age=60"


def _raise(exc: funding.FundingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _parse_month(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return funding.parse_month(value)
    except funding.FundingError as exc:
        raise _raise(exc) from exc


@router.get("/funding/month", response_model=FundingMonthResponse)
def public_funding_month(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    month: Annotated[str | None, Query(description="Месяц ГГГГ-ММ; без него — текущий")] = None,
) -> FundingMonthResponse:
    target = _parse_month(month)
    try:
        result = funding.public_month(db, target)
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    response.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL
    if result is None:
        return FundingMonthResponse(visible=False)
    return FundingMonthResponse(
        visible=True,
        month=funding.format_month(result.month),
        current_month=funding.format_month(result.current_month),
        first_month=funding.format_month(result.first_month),
        last_month=funding.format_month(result.last_month),
        costs=[
            FundingCostOut(title=line.title, hint=line.hint, amount_rub=line.amount_rub)
            for line in result.costs.lines
        ],
        cost_total_rub=result.costs.total_rub,
        donations=[
            FundingDonationOut(received_on=item.received_on, name=item.name, amount_rub=item.amount_rub)
            for item in result.donations
        ],
        donated_total_rub=result.donated_total_rub,
    )


# ---------- админка ----------


def _admin_donation(donation: SiteDonation) -> FundingAdminDonationOut:
    return FundingAdminDonationOut(
        id=donation.id,
        received_on=donation.received_on,
        amount_rub=donation.amount_rub,
        donor_name=donation.donor_name,
        is_anonymous=donation.is_anonymous,
    )


def _admin_month(db: Session, month_value: str | None) -> FundingAdminMonthResponse:
    current = funding.month_start(funding.today_msk())
    month = _parse_month(month_value) or current
    costs = funding.month_costs(db, month)
    donations = funding.month_donations(db, month)
    return FundingAdminMonthResponse(
        month=funding.format_month(month),
        current_month=funding.format_month(current),
        max_month=funding.format_month(funding.add_months(current, funding.PLAN_MONTHS_AHEAD)),
        show_public=funding.get_settings_row(db).show_public,
        items=[FundingItemOut(id=item.id, title=item.title, hint=item.hint) for item in funding.list_items(db)],
        costs=[
            FundingCostLineOut(item_id=line.item_id, title=line.title, hint=line.hint, amount_rub=line.amount_rub)
            for line in costs.lines
        ],
        costs_copied_from=funding.format_month(costs.copied_from) if costs.copied_from else None,
        cost_total_rub=costs.total_rub,
        donations=[_admin_donation(donation) for donation in donations],
        donated_total_rub=sum(donation.amount_rub for donation in donations),
        donor_names=[FundingDonorNameOut(name=name, count=count) for name, count in funding.known_donor_names(db)],
    )


@router.get("/admin/funding", response_model=FundingAdminMonthResponse)
def admin_funding_month(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    month: Annotated[str | None, Query(description="Месяц ГГГГ-ММ; без него — текущий")] = None,
) -> FundingAdminMonthResponse:
    return _admin_month(db, month)


@router.put("/admin/funding/settings", response_model=FundingSettingsResponse)
def admin_funding_settings(
    body: FundingSettingsRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> FundingSettingsResponse:
    try:
        row = funding.set_show_public(db, body.show_public)
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return FundingSettingsResponse(show_public=row.show_public)


@router.put("/admin/funding/costs/{month}", response_model=FundingAdminMonthResponse)
def admin_funding_save_costs(
    month: str,
    body: FundingMonthCostsRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> FundingAdminMonthResponse:
    try:
        target = funding.parse_month(month)
        funding.save_month_costs(db, target, [(line.item_id, line.amount_rub) for line in body.items])
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return _admin_month(db, month)


@router.post("/admin/funding/items", response_model=FundingItemOut, status_code=status.HTTP_201_CREATED)
def admin_funding_create_item(
    body: FundingItemRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> FundingItemOut:
    try:
        item = funding.create_item(db, title=body.title, hint=body.hint)
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return FundingItemOut(id=item.id, title=item.title, hint=item.hint)


@router.put("/admin/funding/items/{item_id}", response_model=FundingItemOut)
def admin_funding_update_item(
    item_id: UUID,
    body: FundingItemRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> FundingItemOut:
    try:
        item = funding.update_item(db, item_id, title=body.title, hint=body.hint)
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return FundingItemOut(id=item.id, title=item.title, hint=item.hint)


@router.post(
    "/admin/funding/donations",
    response_model=FundingAdminDonationOut,
    status_code=status.HTTP_201_CREATED,
)
def admin_funding_create_donation(
    body: FundingDonationRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> FundingAdminDonationOut:
    try:
        donation = funding.create_donation(
            db,
            received_on=body.received_on,
            amount_rub=body.amount_rub,
            donor_name=body.donor_name,
            is_anonymous=body.is_anonymous,
        )
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return _admin_donation(donation)


@router.put("/admin/funding/donations/{donation_id}", response_model=FundingAdminDonationOut)
def admin_funding_update_donation(
    donation_id: UUID,
    body: FundingDonationRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> FundingAdminDonationOut:
    try:
        donation = funding.update_donation(
            db,
            donation_id,
            received_on=body.received_on,
            amount_rub=body.amount_rub,
            donor_name=body.donor_name,
            is_anonymous=body.is_anonymous,
        )
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return _admin_donation(donation)


@router.delete("/admin/funding/donations/{donation_id}", response_model=AbuseMessageResponse)
def admin_funding_delete_donation(
    donation_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        funding.delete_donation(db, donation_id)
    except funding.FundingError as exc:
        raise _raise(exc) from exc
    return AbuseMessageResponse(message="donation_deleted")
