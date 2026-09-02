from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuthIdentity, DashboardCache, Platform, PlatformLink, SyncJob, User
from app.services.auth_identity_service import list_user_identities, merge_preview_payload
from app.services.dashboard_service import recompute_dashboard_cache
from app.services.organizer_access_service import invalidate_organizer_locations_cache
from app.services.profile_unlink_service import unlink_user_profile
from app.services.user_display_name_service import rebind_display_name_source


class AccountMergeError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def delete_user_with_dependencies(
    db: Session,
    merged: User,
    *,
    reassign_sync_jobs_to: UUID | None = None,
) -> None:
    """Delete user row after clearing rows that cannot be nulled on FK cascade."""
    db.query(DashboardCache).filter(DashboardCache.user_id == merged.id).delete()
    if reassign_sync_jobs_to is not None:
        db.query(SyncJob).filter(SyncJob.user_id == merged.id).update({"user_id": reassign_sync_jobs_to})
    else:
        db.query(SyncJob).filter(SyncJob.user_id == merged.id).delete(synchronize_session=False)
    db.flush()
    db.delete(merged)


def merge_users(db: Session, survivor_id: UUID, merged_id: UUID) -> User:
    if survivor_id == merged_id:
        raise AccountMergeError("Нельзя объединить профиль сам с собой.")

    survivor = db.query(User).filter(User.id == survivor_id).one_or_none()
    merged = db.query(User).filter(User.id == merged_id).one_or_none()
    if survivor is None or merged is None:
        raise AccountMergeError("Профиль не найден.", 404)

    platform_codes = [
        row[0]
        for row in db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == merged.id)
        .all()
    ]
    for platform_code in platform_codes:
        unlink_user_profile(db, merged, platform_code)

    merged_identities = list_user_identities(db, merged.id)
    for identity in merged_identities:
        existing = (
            db.query(AuthIdentity)
            .filter(AuthIdentity.user_id == survivor.id, AuthIdentity.provider == identity.provider)
            .one_or_none()
        )
        if existing is not None and existing.id != identity.id:
            db.delete(existing)
        identity.user_id = survivor.id
        if identity.provider.value == "telegram":
            survivor.telegram_id = int(identity.external_id)
            survivor.telegram_username = identity.profile_json.get("telegram_username")
            survivor.telegram_first_name = identity.profile_json.get("telegram_first_name")
            survivor.telegram_last_name = identity.profile_json.get("telegram_last_name")
            chat_id = identity.profile_json.get("telegram_chat_id")
            survivor.telegram_chat_id = int(chat_id) if chat_id is not None else None

    if merged.consent_accepted and not survivor.consent_accepted:
        survivor.consent_accepted = True
        survivor.consent_ts = merged.consent_ts or datetime.now(timezone.utc)

    for platform_code, enabled in (merged.auto_sync_by_platform or {}).items():
        survivor_prefs = survivor.auto_sync_by_platform or {}
        if enabled and not survivor_prefs.get(platform_code):
            survivor_prefs[platform_code] = True
        survivor.auto_sync_by_platform = survivor_prefs

    if merged.news_subscribed and survivor.telegram_chat_id:
        survivor.news_subscribed = True

    delete_user_with_dependencies(db, merged, reassign_sync_jobs_to=survivor.id)
    db.commit()
    db.refresh(survivor)
    recompute_dashboard_cache(db, survivor.id)
    # Привязки и способы входа переехали — источник имени пересматриваем заново.
    rebind_display_name_source(db, survivor, commit=True)
    # К выжившему профилю переехали чужие привязки — доступ мог появиться.
    invalidate_organizer_locations_cache(survivor.id)
    return survivor


def build_merge_preview(db: Session, survivor_id: UUID, merged_id: UUID) -> dict[str, object]:
    survivor = db.query(User).filter(User.id == survivor_id).one_or_none()
    merged = db.query(User).filter(User.id == merged_id).one_or_none()
    if survivor is None or merged is None:
        raise AccountMergeError("Профиль не найден.", 404)
    if survivor.id == merged.id:
        raise AccountMergeError("Нельзя объединить профиль сам с собой.")
    return merge_preview_payload(db, survivor, merged)
