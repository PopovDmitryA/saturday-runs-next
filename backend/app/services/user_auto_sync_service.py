from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Platform, PlatformLink, SyncJobTrigger, User

AUTO_SYNC_PLATFORM_CODES = ("five_verst", "s95", "parkrun")

DEFAULT_AUTO_SYNC_BY_PLATFORM: dict[str, bool] = {
    code: False for code in AUTO_SYNC_PLATFORM_CODES
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_auto_sync_preferences(raw: dict[str, object] | None) -> dict[str, bool]:
    merged = dict(DEFAULT_AUTO_SYNC_BY_PLATFORM)
    if not raw:
        return merged
    for code in AUTO_SYNC_PLATFORM_CODES:
        value = raw.get(code)
        if isinstance(value, bool):
            merged[code] = value
    return merged


def get_auto_sync_preferences(user: User) -> dict[str, bool]:
    return normalize_auto_sync_preferences(user.auto_sync_by_platform)


def enabled_auto_sync_platform_codes(user: User) -> list[str]:
    prefs = get_auto_sync_preferences(user)
    return [code for code in AUTO_SYNC_PLATFORM_CODES if prefs.get(code)]


def update_auto_sync_preferences(user: User, updates: dict[str, bool]) -> dict[str, bool]:
    prefs = get_auto_sync_preferences(user)
    for code, enabled in updates.items():
        if code in AUTO_SYNC_PLATFORM_CODES:
            prefs[code] = enabled
    user.auto_sync_by_platform = prefs
    return prefs


def login_auto_sync_due(user: User, *, interval_seconds: int, now: datetime | None = None) -> bool:
    if not enabled_auto_sync_platform_codes(user):
        return False
    if user.last_login_auto_sync_at is None:
        return True
    current = now or _utcnow()
    age = (current - user.last_login_auto_sync_at).total_seconds()
    return age >= interval_seconds


def maybe_enqueue_login_auto_sync(
    db: Session,
    user_id: UUID,
    *,
    interval_seconds: int,
) -> bool:
    from app.services.sync_enqueue_service import enqueue_sync_for_platform_codes

    user = db.query(User).filter(User.id == user_id).one()
    enabled_codes = enabled_auto_sync_platform_codes(user)
    if not enabled_codes:
        return False
    if not login_auto_sync_due(user, interval_seconds=interval_seconds):
        return False

    linked_codes = {
        platform.code
        for _link, platform in (
            db.query(PlatformLink, Platform)
            .join(Platform, PlatformLink.platform_id == Platform.id)
            .filter(PlatformLink.user_id == user_id)
            .all()
        )
    }
    codes_to_sync = [code for code in enabled_codes if code in linked_codes]
    if not codes_to_sync:
        return False

    enqueue_sync_for_platform_codes(db, user_id, SyncJobTrigger.login, codes_to_sync)
    user.last_login_auto_sync_at = _utcnow()
    db.commit()
    return True
