from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import Platform, PlatformLink, User
from app.schemas.settings import (
    AutoSyncPlatformPreference,
    AutoSyncSettingsResponse,
    AutoSyncSettingsUpdateRequest,
    NotificationSettingsResponse,
    NotificationSettingsUpdateRequest,
)
from app.services.user_auto_sync_service import (
    AUTO_SYNC_PLATFORM_CODES,
    get_auto_sync_preferences,
    update_auto_sync_preferences,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/auto-sync", response_model=AutoSyncSettingsResponse)
def get_auto_sync_settings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AutoSyncSettingsResponse:
    prefs = get_auto_sync_preferences(user)
    linked_codes = {
        platform.code
        for _link, platform in (
            db.query(PlatformLink, Platform)
            .join(Platform, PlatformLink.platform_id == Platform.id)
            .filter(PlatformLink.user_id == user.id)
            .all()
        )
    }
    platforms = [
        AutoSyncPlatformPreference(
            platform_code=code,
            enabled=prefs.get(code, False),
            linked=code in linked_codes,
        )
        for code in AUTO_SYNC_PLATFORM_CODES
    ]
    return AutoSyncSettingsResponse(
        interval_hours=max(1, settings.user_login_auto_sync_interval_seconds // 3600),
        last_login_auto_sync_at=user.last_login_auto_sync_at,
        platforms=platforms,
    )


@router.put("/auto-sync", response_model=AutoSyncSettingsResponse)
def update_auto_sync_settings(
    body: AutoSyncSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AutoSyncSettingsResponse:
    update_auto_sync_preferences(user, body.auto_sync_by_platform)
    db.commit()
    db.refresh(user)
    return get_auto_sync_settings(db, user, settings)


@router.get("/notifications", response_model=NotificationSettingsResponse)
def get_notification_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(enabled=user.news_subscribed)


@router.put("/notifications", response_model=NotificationSettingsResponse)
def update_notification_settings(
    body: NotificationSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsResponse:
    user.news_subscribed = body.enabled
    db.commit()
    db.refresh(user)
    return NotificationSettingsResponse(enabled=user.news_subscribed)
