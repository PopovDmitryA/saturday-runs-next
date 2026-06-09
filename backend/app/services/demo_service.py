from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.admin import admin_email_set, effective_admin_telegram_id, find_admin_user
from app.models import AuthIdentity, AuthProvider, User
from app.services.admin_users_service import (
    get_admin_user_preview_dashboard,
)
from app.services.dashboard_service import (
    get_dashboard_payload,
)


def _user_by_telegram_id(db: Session, telegram_id: int) -> User | None:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user is not None:
        return user
    identity = (
        db.query(AuthIdentity)
        .filter(
            AuthIdentity.provider == AuthProvider.telegram,
            AuthIdentity.external_id == str(telegram_id),
        )
        .one_or_none()
    )
    if identity is None:
        return None
    return db.query(User).filter(User.id == identity.user_id).one_or_none()


def get_demo_user(db: Session) -> User:
    settings = get_settings()

    if settings.demo_user_id:
        try:
            demo_id = UUID(settings.demo_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="Demo profile is not configured") from exc
        user = db.query(User).filter(User.id == demo_id).one_or_none()
        if user is not None:
            return user

    if settings.demo_telegram_id:
        user = _user_by_telegram_id(db, settings.demo_telegram_id)
        if user is not None:
            return user

    user = find_admin_user(db, settings)
    if user is not None:
        return user

    if (
        not settings.demo_user_id
        and not settings.demo_telegram_id
        and effective_admin_telegram_id(settings) <= 0
        and not admin_email_set(settings)
    ):
        raise HTTPException(status_code=503, detail="Demo profile is not configured")
    raise HTTPException(status_code=404, detail="Demo profile not found")


def get_demo_user_id(db: Session) -> UUID:
    return get_demo_user(db).id


def get_demo_dashboard(db: Session) -> dict[str, object]:
    user = get_demo_user(db)
    payload = get_admin_user_preview_dashboard(db, user.id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Demo profile not found")
    dashboard = get_dashboard_payload(db, user)
    return {
        **payload,
        "stats": dashboard["stats"],
        "computed_at": dashboard["computed_at"],
        "sync_enqueued": False,
    }
