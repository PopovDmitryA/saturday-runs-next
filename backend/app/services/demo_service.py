from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.services.admin_users_service import (
    get_admin_user_preview_dashboard,
)
from app.services.dashboard_service import (
    get_dashboard_payload,
)


def resolve_demo_telegram_id() -> int:
    settings = get_settings()
    if settings.demo_telegram_id:
        return settings.demo_telegram_id
    if settings.admin_telegram_id:
        return settings.admin_telegram_id
    return 0


def get_demo_user(db: Session) -> User:
    telegram_id = resolve_demo_telegram_id()
    if not telegram_id:
        raise HTTPException(status_code=503, detail="Demo profile is not configured")
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Demo profile not found")
    return user


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
