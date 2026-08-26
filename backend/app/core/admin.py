from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuthIdentity, AuthProvider, User
from app.schemas.auth import AuthIdentityResponse, UserResponse
from app.services.auth_identity_service import identity_response_payload


def effective_admin_telegram_id(settings: Settings) -> int:
    if settings.admin_telegram_id > 0:
        return settings.admin_telegram_id
    # Fallback: в .env часто задан только TELEGRAM_ADMIN_CHAT_ID (для личного чата совпадает с user id).
    return settings.telegram_admin_chat_id


def admin_email_set(settings: Settings) -> frozenset[str]:
    if not settings.admin_emails.strip():
        return frozenset()
    return frozenset(part.strip().lower() for part in settings.admin_emails.split(",") if part.strip())


def identity_emails(identity: AuthIdentity) -> frozenset[str]:
    emails: set[str] = set()
    if identity.email:
        emails.add(identity.email.strip().lower())
    profile = identity.profile_json or {}
    for key in ("default_email", "email"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            emails.add(value.strip().lower())
    return frozenset(emails)


def is_admin_user(user: User, settings: Settings) -> bool:
    allowed_emails = admin_email_set(settings)
    if allowed_emails:
        for identity in user.auth_identities:
            if allowed_emails.intersection(identity_emails(identity)):
                return True

    admin_id = effective_admin_telegram_id(settings)
    if admin_id <= 0:
        return False
    if user.telegram_id == admin_id:
        return True
    for identity in user.auth_identities:
        if identity.provider == AuthProvider.telegram and identity.external_id == str(admin_id):
            return True
    return False


def is_admin_telegram_id(telegram_id: int, settings: Settings) -> bool:
    admin_id = effective_admin_telegram_id(settings)
    return admin_id > 0 and telegram_id == admin_id


def is_admin_vk_user_id(vk_user_id: int, settings: Settings) -> bool:
    return settings.vk_admin_user_id > 0 and vk_user_id == settings.vk_admin_user_id


def find_admin_user(db: Session, settings: Settings) -> User | None:
    """Resolve the configured admin account in DB (telegram id, telegram identity, or admin email)."""
    allowed_emails = admin_email_set(settings)
    if allowed_emails:
        identities = db.query(AuthIdentity).all()
        for identity in identities:
            if allowed_emails.intersection(identity_emails(identity)):
                user = db.query(User).filter(User.id == identity.user_id).one_or_none()
                if user is not None:
                    return user

    admin_id = effective_admin_telegram_id(settings)
    if admin_id > 0:
        user = db.query(User).filter(User.telegram_id == admin_id).one_or_none()
        if user is not None:
            return user
        identity = (
            db.query(AuthIdentity)
            .filter(
                AuthIdentity.provider == AuthProvider.telegram,
                AuthIdentity.external_id == str(admin_id),
            )
            .one_or_none()
        )
        if identity is not None:
            return db.query(User).filter(User.id == identity.user_id).one_or_none()
    return None


# Поля ответа, которых нет в модели User: их передают отдельно.
_COMPUTED_USER_FIELDS = {"is_admin", "is_organizer", "auth_identities", "display_name_suggestion"}



def user_response(
    user: User,
    settings: Settings,
    db_identities: list | None = None,
    *,
    db: Session | None = None,
    display_name_suggestion: dict | None = None,
) -> UserResponse:
    identities = db_identities or list(user.auth_identities)
    identity_responses = [
        AuthIdentityResponse.model_validate(identity_response_payload(item)) for item in identities
    ]
    scalar_fields = {
        name: getattr(user, name)
        for name in UserResponse.model_fields
        if name not in _COMPUTED_USER_FIELDS
    }
    is_organizer = False
    if db is not None:
        from app.services.organizer_access_service import user_is_organizer

        is_organizer = user_is_organizer(db, user)
    return UserResponse(
        **scalar_fields,
        is_admin=is_admin_user(user, settings),
        is_organizer=is_organizer,
        auth_identities=identity_responses,
        display_name_suggestion=display_name_suggestion,
    )
