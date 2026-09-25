from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import AuthProvider, Platform, PlatformLink, User
from app.schemas.notifications import (
    NewsletterSettingsResponse,
    NewsletterSettingsUpdateRequest,
    NotificationChannelToggle,
    NotificationCheckResponse,
    NotificationConnectUrlResponse,
    NotificationEnableResponse,
    NotificationNudgeState,
    NotificationSettingsState,
    NotificationSettingsUpdate,
    NotificationTestResponse,
)
from app.schemas.settings import (
    AutoSyncPlatformPreference,
    AutoSyncSettingsResponse,
    AutoSyncSettingsUpdateRequest,
    HistoryMilestoneBulkUpdateRequest,
    HistoryMilestoneKindUpdateRequest,
    HistoryMilestoneSettingsResponse,
    HomeLocationCandidateResponse,
    HomeLocationResponse,
    HomeLocationUpdateRequest,
    PrivacySettingsResponse,
    PrivacySettingsUpdateRequest,
    ProfileSlugCheckResponse,
    ProfileSlugResponse,
    ProfileSlugUpdateRequest,
    TourismPlatformsResponse,
    TourismPlatformsUpdateRequest,
)
from app.services import notification_channels_service as channels
from app.services import notification_service as notifications
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
    set_user_disabled_milestone_kinds,
    set_user_milestone_kind_enabled,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _profile_slug_response(user: User, settings: Settings) -> ProfileSlugResponse:
    public_url = f"{settings.app_base_url.rstrip('/')}/users/{user.public_slug}" if user.public_slug else None
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


@router.get("/newsletter", response_model=NewsletterSettingsResponse)
def get_newsletter_settings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NewsletterSettingsResponse:
    return NewsletterSettingsResponse(
        enabled=user.news_subscribed,
        email=_newsletter_email(db, user),
    )


@router.put("/newsletter", response_model=NewsletterSettingsResponse)
def update_newsletter_settings(
    body: NewsletterSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NewsletterSettingsResponse:
    user.news_subscribed = body.enabled
    db.commit()
    db.refresh(user)
    return NewsletterSettingsResponse(
        enabled=user.news_subscribed,
        email=_newsletter_email(db, user),
    )


# --- Уведомления сайта (app/services/notification_service.py) ---------------


def _notification_state(db: Session, user: User) -> NotificationSettingsState:
    state = NotificationSettingsState.model_validate(notifications.settings_state(db, user))
    # channels_state мог обновить check_ok у строк каналов — фиксируем.
    db.commit()
    return state


@router.get("/notifications", response_model=NotificationSettingsState)
def get_notification_settings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsState:
    return _notification_state(db, user)


@router.put("/notifications", response_model=NotificationSettingsState)
def update_notification_settings(
    body: NotificationSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsState:
    """Основной канал и переключатели видов — частично: приходят только изменённые поля."""
    fields = body.model_dump(exclude_unset=True)
    try:
        notifications.update_prefs(
            db,
            user.id,
            primary_channel=fields["primary_channel"] if "primary_channel" in fields else ...,
            kinds=fields.get("kinds"),
            cancellation_platforms=fields.get("cancellation_platforms"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return _notification_state(db, user)


@router.put("/notifications/channels/{channel}", response_model=NotificationSettingsState)
def toggle_notification_channel(
    channel: str,
    body: NotificationChannelToggle,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationSettingsState:
    """Переключатель «уведомления» у способа входа."""
    try:
        notifications.set_channel_enabled(db, user, channel, body.enabled)
    except channels.ChannelError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    return _notification_state(db, user)


@router.post("/notifications/channels/{channel}/check", response_model=NotificationCheckResponse)
def check_notification_channel(
    channel: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationCheckResponse:
    """«Проверить ещё раз»: заново спросить бота или VK, можно ли писать."""
    if channel not in channels.CHANNEL_ORDER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Неизвестный канал")
    outcome = channels.recheck_channel(db, user, channel)
    db.commit()
    return NotificationCheckResponse(ok=outcome.ok, error=outcome.error)


@router.post("/notifications/channels/telegram/connect", response_model=NotificationConnectUrlResponse)
def telegram_notification_connect(
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationConnectUrlResponse:
    """Ссылка в бота: по /start он сообщит chat_id, и канал включится."""
    url = channels.telegram_connect_url(user)
    if url is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telegram-бот не настроен")
    return NotificationConnectUrlResponse(connect_url=url)


@router.get("/notifications/nudge", response_model=NotificationNudgeState)
def notification_nudge(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationNudgeState:
    """Показывать ли баннер «Включите уведомления»."""
    return NotificationNudgeState.model_validate(notifications.nudge_state(db, user))


@router.post("/notifications/nudge/dismiss", response_model=NotificationNudgeState)
def dismiss_notification_nudge(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationNudgeState:
    notifications.dismiss_nudge(db, user.id)
    db.commit()
    return NotificationNudgeState.model_validate(notifications.nudge_state(db, user))


@router.post("/notifications/enable", response_model=NotificationEnableResponse)
def enable_notifications(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationEnableResponse:
    """Кнопка «Включить уведомления» из баннера или модалки: лучший доступный канал."""
    channel = notifications.enable_now(db, user)
    db.commit()
    return NotificationEnableResponse(channel=channel, state=_notification_state(db, user))


@router.post("/notifications/test", response_model=NotificationTestResponse)
def send_notification_test(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NotificationTestResponse:
    """Проверочное сообщение по включённым каналам (для стенда и отладки; в
    интерфейсе кнопки нет). Повтор в ту же минуту отбрасывается."""
    enabled = channels.enabled_channels(db, user.id)
    if not enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ни один канал не включён")
    delivery = notifications.send_test_notification(db, user)
    return NotificationTestResponse(queued=delivery is not None, channels=enabled)


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


@router.get("/tourism-platforms", response_model=TourismPlatformsResponse)
def get_tourism_platforms(
    user: Annotated[User, Depends(get_current_user)],
) -> TourismPlatformsResponse:
    return TourismPlatformsResponse(platforms=list(user.tourism_platforms or []))


@router.put("/tourism-platforms", response_model=TourismPlatformsResponse)
def update_tourism_platforms(
    body: TourismPlatformsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> TourismPlatformsResponse:
    """Системы для плитки «Куда дальше» — из модалки «показывать на плитке только…».

    Пустой список — все системы. Плитка живёт в кэше аналитики дашборда,
    поэтому кэш сбрасываем: иначе ближайшая площадка ещё сутки была бы старой.
    """
    known = {code for (code,) in db.query(Platform.code).all()}
    platforms: list[str] = []
    for code in body.platforms:
        normalized = code.strip().lower()
        if normalized not in known:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неизвестная система: {code}",
            )
        if normalized not in platforms:
            platforms.append(normalized)
    user.tourism_platforms = platforms
    invalidate_dashboard_cache_for_users(db, {user.id})
    db.commit()
    db.refresh(user)
    return TourismPlatformsResponse(platforms=list(user.tourism_platforms or []))


@router.get("/history-milestones", response_model=HistoryMilestoneSettingsResponse)
def get_history_milestone_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> HistoryMilestoneSettingsResponse:
    return HistoryMilestoneSettingsResponse.model_validate({"kinds": list_user_milestone_kind_settings(user)})


@router.put("/history-milestones", response_model=HistoryMilestoneSettingsResponse)
def replace_history_milestone_settings(
    body: HistoryMilestoneBulkUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HistoryMilestoneSettingsResponse:
    try:
        set_user_disabled_milestone_kinds(user, body.disabled_kinds)
    except UnknownMilestoneKindError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Неизвестный вид вехи"
        ) from err
    db.commit()
    db.refresh(user)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Неизвестный вид вехи") from err
    db.commit()
    db.refresh(user)
    return HistoryMilestoneSettingsResponse.model_validate({"kinds": list_user_milestone_kind_settings(user)})


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
