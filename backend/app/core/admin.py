from __future__ import annotations

from app.config import Settings
from app.models import AuthProvider, User
from app.schemas.auth import AuthIdentityResponse, UserResponse
from app.services.auth_identity_service import identity_response_payload, list_user_identities


def effective_admin_telegram_id(settings: Settings) -> int:
    if settings.admin_telegram_id > 0:
        return settings.admin_telegram_id
    # Fallback: в .env часто задан только TELEGRAM_ADMIN_CHAT_ID (для личного чата совпадает с user id).
    return settings.telegram_admin_chat_id


def is_admin_user(user: User, settings: Settings) -> bool:
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


def user_response(user: User, settings: Settings, db_identities: list | None = None) -> UserResponse:
    identities = db_identities or list(user.auth_identities)
    identity_responses = [
        AuthIdentityResponse.model_validate(identity_response_payload(item)) for item in identities
    ]
    scalar_fields = {
        name: getattr(user, name)
        for name in UserResponse.model_fields
        if name not in {"is_admin", "auth_identities"}
    }
    return UserResponse(
        **scalar_fields,
        is_admin=is_admin_user(user, settings),
        auth_identities=identity_responses,
    )
