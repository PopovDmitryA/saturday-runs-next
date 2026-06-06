from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.abuse_store import is_telegram_banned
from app.core.admin import is_admin_user
from app.core.session import get_session_user_id
from app.db.session import get_db
from app.models import User
from app.schemas.auth import UserResponse


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


def get_optional_session_user_id(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> UUID | None:
    signed = request.cookies.get(settings.session_cookie_name)
    if signed is None:
        return None
    user_id = get_session_user_id(settings, signed)
    if user_id is None:
        return None
    return UUID(user_id)


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[UUID | None, Depends(get_optional_session_user_id)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    from app.core.abuse_store import remember_user_ip

    if user.telegram_id is not None:
        remember_user_ip(user.telegram_id, get_client_ip(request))
        if is_telegram_banned(user.telegram_id) and not is_admin_user(user, settings):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account temporarily suspended")
    return user


def get_current_user_response(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return UserResponse.model_validate(user)


def get_current_admin_user(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if not is_admin_user(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
