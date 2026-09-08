"""Заявки на волонтёрство со стороны участника (страница локации).

Организаторская половина — в routes/organizer.py (нужна проверка доступа к
локации). Здесь только свои заявки: посмотреть варианты, подать, отозвать.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.volunteer_signup import (
    SignupCreateRequest,
    SignupCreateResponse,
    SignupOptionsResponse,
)
from app.services.location_page_service import resolve_location_identity
from app.services.volunteer_signup_service import (
    SignupError,
    build_signup_options,
    cancel_signup_request,
    create_signup_request,
    serialize_request,
)

router = APIRouter(prefix="/volunteer-signup", tags=["volunteer-signup"])


def _identity(db: Session, slug: str):
    identity = resolve_location_identity(db, slug)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return identity


@router.get("/{slug}", response_model=SignupOptionsResponse)
def signup_options(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SignupOptionsResponse:
    identity = _identity(db, slug)
    return SignupOptionsResponse.model_validate(build_signup_options(db, identity, user))


@router.post("/{slug}", response_model=SignupCreateResponse, status_code=status.HTTP_201_CREATED)
def signup_create(
    slug: str,
    payload: SignupCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SignupCreateResponse:
    identity = _identity(db, slug)
    try:
        request = create_signup_request(
            db,
            identity,
            user,
            event_date=payload.event_date,
            role_name=payload.role_name,
            comment=payload.comment,
        )
    except SignupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return SignupCreateResponse(item=serialize_request(request, roster=None))


@router.delete("/{slug}/{request_id}", response_model=SignupCreateResponse)
def signup_cancel(
    slug: str,
    request_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SignupCreateResponse:
    _identity(db, slug)
    try:
        request = cancel_signup_request(db, user, request_id)
    except SignupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return SignupCreateResponse(item=serialize_request(request, roster=None))
