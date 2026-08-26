from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import AuthProvider, Platform, PlatformLink, User
from app.schemas.settings import (
    AutoSyncPlatformPreference,
    AutoSyncSettingsResponse,
    AutoSyncSettingsUpdateRequest,
    HistoryMilestoneKindUpdateRequest,
    HistoryMilestoneSettingsResponse,
    HomeLocationCandidateResponse,
    HomeLocationResponse,
    HomeLocationUpdateRequest,
    NotificationSettingsResponse,
    NotificationSettingsUpdateRequest,
    PrivacySettingsResponse,
    PrivacySettingsUpdateRequest,
    ProfileSlugCheckResponse,
    ProfileSlugResponse,
    ProfileSlugUpdateRequest,
)
from app.services.auth_identity_service import list_user_identities
from app.services.dashboard_service import invalidate_dashboard_cache_for_users
from app.services.home_location_service import (
    UnknownHomeLocationError,
    list_home_location_candidates,
    resolve_home_location,
    set_home_location,
)
from app.services.profile_slug_service import (
    SLUG_MAX_LEN,
    SLUG_MIN_LEN,
    SlugError,
    check_slug_availability,
    set_profile_slug,
)
from app.services.user_auto_sync_service import (
    AUTO_SYNC_PLATFORM_CODES,
    get_auto_sync_preferences,
    update_auto_sync_preferences,
)
from app.services.user_history_milestone_service import (
    UnknownMilestoneKindError,
    list_user_milestone_kind_settings,
    set_user_milestone_kind_enabled,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _profile_slug_response(user: User, settings: Settings) -> ProfileSlugResponse:
    public_url = (
        f"{settings.app_base_url.rstrip('/')}/users/{user.public_slug}"
        if user.public_slug
        else None
    )
    return ProfileSlugResponse(
        slug=user.public_slug,
        public_url=public_url,
        min_length=SLUG_MIN_LEN,
        max_length=SLUG_MAX_LEN,
    )


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


def _newsletter_email(db: Session, user: User) -> str | None:
    """Адрес, на который уйдёт рассылка: почтовая привязка, иначе адрес от Яндекса."""
    identities = list_user_identities(db, user.id)
    for identity in identities:
        if identity.provider == AuthProvider.email and identity.email:
            return identity.email
    for identity in identities:
        if identity.email:
            return identity.email
    return None


@router.get("/notifications", response_model=NotificationSettingsResponse)
def get_notification_settings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        enabled=user.news_subscribed,
        email=_newsletter_email(db, user),
    )


@router.put("/notifications", response_model=NotificationSettingsResponse)
def update_notification_settings(
    body: NotificationSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsResponse:
    user.news_subscribed = body.enabled
    db.commit()
    db.refresh(user)
    return NotificationSettingsResponse(
        enabled=user.news_subscribed,
        email=_newsletter_email(db, user),
    )


@router.get("/privacy", response_model=PrivacySettingsResponse)
def get_privacy_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> PrivacySettingsResponse:
    return PrivacySettingsResponse(enabled=user.profile_private)


@router.put("/privacy", response_model=PrivacySettingsResponse)
def update_privacy_settings(
    body: PrivacySettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> PrivacySettingsResponse:
    user.profile_private = body.enabled
    db.commit()
    db.refresh(user)
    return PrivacySettingsResponse(enabled=user.profile_private)


@router.get("/home-location", response_model=HomeLocationResponse)
def get_home_location(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HomeLocationResponse:
    candidate, is_auto = resolve_home_location(db, user)
    return HomeLocationResponse(
        location=HomeLocationCandidateResponse.model_validate(candidate) if candidate else None,
        is_auto=is_auto,
    )


@router.get("/home-location/candidates", response_model=list[HomeLocationCandidateResponse])
def get_home_location_candidates(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[HomeLocationCandidateResponse]:
    candidates = list_home_location_candidates(db, user.id)
    return [HomeLocationCandidateResponse.model_validate(candidate) for candidate in candidates]


@router.put("/home-location", response_model=HomeLocationResponse)
def update_home_location(
    body: HomeLocationUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HomeLocationResponse:
    try:
        set_home_location(db, user, body.catalog_identity_key)
    except UnknownHomeLocationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Локация не найдена среди ваших посещённых",
        ) from err
    # От домашней локации считается вся «дальность стартов», а она лежит в
    # кэше аналитики дашборда: без сброса плитка на главной ещё сутки
    # показывала бы километры от старого дома.
    invalidate_dashboard_cache_for_users(db, {user.id})
    db.commit()
    db.refresh(user)
    return get_home_location(db, user)


@router.get("/history-milestones", response_model=HistoryMilestoneSettingsResponse)
def get_history_milestone_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> HistoryMilestoneSettingsResponse:
    return HistoryMilestoneSettingsResponse.model_validate(
        {"kinds": list_user_milestone_kind_settings(user)}
    )


@router.put("/history-milestones/{kind}", response_model=HistoryMilestoneSettingsResponse)
def update_history_milestone_setting(
    kind: str,
    body: HistoryMilestoneKindUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HistoryMilestoneSettingsResponse:
    try:
        set_user_milestone_kind_enabled(user, kind, body.enabled)
    except UnknownMilestoneKindError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Неизвестный вид вехи"
        ) from err
    db.commit()
    db.refresh(user)
    return HistoryMilestoneSettingsResponse.model_validate(
        {"kinds": list_user_milestone_kind_settings(user)}
    )


@router.get("/profile-slug", response_model=ProfileSlugResponse)
def get_profile_slug(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProfileSlugResponse:
    return _profile_slug_response(user, settings)


@router.get("/profile-slug/check", response_model=ProfileSlugCheckResponse)
def check_profile_slug(
    slug: Annotated[str, Query()],
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileSlugCheckResponse:
    available, reason, normalized = check_slug_availability(db, user, slug)
    return ProfileSlugCheckResponse(normalized=normalized, available=available, reason=reason)


@router.put("/profile-slug", response_model=ProfileSlugResponse)
def update_profile_slug(
    body: ProfileSlugUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProfileSlugResponse:
    try:
        set_profile_slug(db, user, body.slug)
    except SlugError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    db.commit()
    db.refresh(user)
    return _profile_slug_response(user, settings)
