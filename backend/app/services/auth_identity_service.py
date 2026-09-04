from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.providers.base import OAuthProfile
from app.models import AuthIdentity, AuthProvider, Participant, Platform, PlatformLink, User

PROVIDER_LABELS = {
    AuthProvider.telegram: "Telegram",
    AuthProvider.vk: "VK",
    AuthProvider.yandex: "Яндекс",
    AuthProvider.email: "Почта",
}


def provider_label(provider: AuthProvider) -> str:
    return PROVIDER_LABELS.get(provider, provider.value)


def find_identity(db: Session, provider: AuthProvider, external_id: str) -> AuthIdentity | None:
    return (
        db.query(AuthIdentity)
        .filter(AuthIdentity.provider == provider, AuthIdentity.external_id == external_id)
        .one_or_none()
    )


def find_user_by_telegram_id(db: Session, telegram_id: int) -> User | None:
    identity = find_identity(db, AuthProvider.telegram, str(telegram_id))
    if identity is not None:
        return identity.user
    return db.query(User).filter(User.telegram_id == telegram_id).one_or_none()


def list_user_identities(db: Session, user_id: UUID) -> list[AuthIdentity]:
    return (
        db.query(AuthIdentity)
        .filter(AuthIdentity.user_id == user_id)
        .order_by(AuthIdentity.linked_at.asc())
        .all()
    )


def user_has_provider(db: Session, user_id: UUID, provider: AuthProvider) -> bool:
    return (
        db.query(AuthIdentity.id)
        .filter(AuthIdentity.user_id == user_id, AuthIdentity.provider == provider)
        .first()
        is not None
    )


def identity_response_payload(identity: AuthIdentity) -> dict[str, object]:
    return {
        "provider": identity.provider.value,
        "external_id": identity.external_id,
        "display_name": identity.display_name,
        "email": identity.email,
        "linked_at": identity.linked_at,
        "label": provider_label(identity.provider),
    }


def upsert_telegram_identity(
    db: Session,
    user: User,
    *,
    telegram_id: int,
    telegram_username: str | None,
    telegram_first_name: str | None,
    telegram_last_name: str | None,
    telegram_chat_id: int | None,
) -> AuthIdentity:
    display_name = _telegram_display_name(
        first_name=telegram_first_name,
        last_name=telegram_last_name,
        username=telegram_username,
    )
    user.telegram_id = telegram_id
    user.telegram_username = telegram_username
    user.telegram_first_name = telegram_first_name
    user.telegram_last_name = telegram_last_name
    user.telegram_chat_id = telegram_chat_id
    # Имя от провайдера входа — только запасной вариант: у привязанного человека
    # оно проигрывает имени из бегового профиля (см. user_display_name_service).
    if display_name and not user.display_name:
        user.display_name = display_name

    identity = find_identity(db, AuthProvider.telegram, str(telegram_id))
    profile_json = {
        "telegram_username": telegram_username,
        "telegram_first_name": telegram_first_name,
        "telegram_last_name": telegram_last_name,
        "telegram_chat_id": telegram_chat_id,
    }
    now = datetime.now(timezone.utc)
    if identity is None:
        identity = AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.telegram,
            external_id=str(telegram_id),
            display_name=display_name,
            profile_json=profile_json,
            last_login_at=now,
        )
        db.add(identity)
    else:
        identity.user_id = user.id
        identity.display_name = display_name
        identity.profile_json = profile_json
        identity.last_login_at = now
    return identity


def create_oauth_user(db: Session, profile: OAuthProfile, provider: AuthProvider, *, consent: bool) -> User:
    now = datetime.now(timezone.utc)
    user = User(
        display_name=profile.display_name,
        consent_accepted=consent,
        consent_ts=now if consent else None,
    )
    db.add(user)
    db.flush()
    identity = AuthIdentity(
        user_id=user.id,
        provider=provider,
        external_id=profile.external_id,
        display_name=profile.display_name,
        email=profile.email,
        profile_json=profile.profile_json,
        last_login_at=now,
    )
    db.add(identity)
    return user


def upsert_oauth_identity(
    db: Session,
    user: User,
    provider: AuthProvider,
    profile: OAuthProfile,
) -> AuthIdentity:
    identity = find_identity(db, provider, profile.external_id)
    now = datetime.now(timezone.utc)
    if identity is None:
        identity = AuthIdentity(
            user_id=user.id,
            provider=provider,
            external_id=profile.external_id,
            display_name=profile.display_name,
            email=profile.email,
            profile_json=profile.profile_json,
            last_login_at=now,
        )
        db.add(identity)
    else:
        identity.user_id = user.id
        identity.display_name = profile.display_name
        identity.email = profile.email
        identity.profile_json = profile.profile_json
        identity.last_login_at = now
    if profile.display_name and not user.display_name:
        user.display_name = profile.display_name
    return identity


def unlink_provider(db: Session, user: User, provider: AuthProvider) -> None:
    identities = list_user_identities(db, user.id)
    if len(identities) <= 1:
        raise ValueError("Нельзя отвязать последний способ входа.")
    identity = next((item for item in identities if item.provider == provider), None)
    if identity is None:
        raise ValueError("Способ входа не привязан.")
    if provider == AuthProvider.telegram:
        user.telegram_id = None
        user.telegram_username = None
        user.telegram_first_name = None
        user.telegram_last_name = None
        user.telegram_chat_id = None
    db.delete(identity)


def list_user_platform_links(db: Session, user_id: UUID) -> list[dict[str, str | None]]:
    """Привязанные учётки беговых систем. display_name — имя из профиля системы:
    без него выбор «чей профиль оставить» при объединении показывал бы человеку
    два голых внешних идентификатора."""
    rows = (
        db.query(Platform.code, PlatformLink.external_user_id, Participant.display_name)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .outerjoin(Participant, Participant.id == PlatformLink.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .order_by(Platform.code.asc())
        .all()
    )
    return [
        {"platform_code": code, "external_user_id": external_user_id, "display_name": display_name}
        for code, external_user_id, display_name in rows
    ]


def _telegram_display_name(
    *,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
) -> str | None:
    parts = [part.strip() for part in (first_name, last_name) if part and part.strip()]
    if parts:
        return " ".join(parts)
    if username:
        return username.lstrip("@")
    return None


#: Привязки обоих аккаунтов остаются на выжившем.
MERGE_STRATEGY_UNION = "union"
#: Остаются только привязки того аккаунта, под которым человек залогинен.
MERGE_STRATEGY_SURVIVOR_ONLY = "survivor_only"
MERGE_STRATEGIES = (MERGE_STRATEGY_UNION, MERGE_STRATEGY_SURVIVOR_ONLY)

#: Чей профиль оставить по системе, привязанной в обоих аккаунтах.
CONFLICT_KEEP_SURVIVOR = "survivor"
CONFLICT_KEEP_MERGED = "merged"


def merge_preview_payload(db: Session, survivor: User, merged: User) -> dict[str, object]:
    """Что человек увидит до объединения.

    Развилка возникает, только когда у присоединяемого аккаунта есть привязки:
    иначе объединять нечего и вопрос был бы шумом. Отдельно считаем конфликты —
    системы, привязанные с обеих сторон: одна учётка системы на аккаунт,
    поэтому там придётся выбрать один профиль из двух.
    """
    merged_links = list_user_platform_links(db, merged.id)
    survivor_links = list_user_platform_links(db, survivor.id)
    survivor_by_code = {item["platform_code"]: item for item in survivor_links}

    conflicts = []
    for item in merged_links:
        rival = survivor_by_code.get(item["platform_code"])
        if rival is not None:
            conflicts.append({"platform_code": item["platform_code"], "survivor": rival, "merged": item})

    return {
        "merged_user_id": str(merged.id),
        "survivor_links": survivor_links,
        "merged_links": merged_links,
        "conflicts": conflicts,
        # Нечего переносить — нечего и спрашивать: объединяем молча.
        "requires_choice": bool(merged_links),
        "default_strategy": MERGE_STRATEGY_UNION,
        "warning": (
            "Способы входа обоих профилей останутся у вас — войти можно будет любым. "
            "Отвязанные учётки систем не пропадают из общей базы, их можно привязать заново."
        ),
    }


def serialize_merge_preview(preview: dict[str, object]) -> str:
    return json.dumps(preview, ensure_ascii=False, default=str)
