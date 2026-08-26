from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.providers import vk as vk_provider
from app.auth.providers import yandex as yandex_provider
from app.auth.providers.base import OAuthProfile
from app.config import Settings
from app.core import email_address
from app.core.redis_client import get_redis_client
from app.core.security import generate_token
from app.core.signup_guard import (
    SignupContext,
    check_signup_allowed,
    record_block,
    register_signup,
    signup_block_message,
)
from app.models import AuthProvider, User
from app.services.account_merge_service import (
    AccountMergeError,
    build_merge_preview,
    delete_user_with_dependencies,
    merge_users,
)
from app.services.auth_identity_service import (
    create_oauth_user,
    find_identity,
    list_user_identities,
    list_user_platform_links,
    merge_preview_payload,
    upsert_oauth_identity,
)
from app.services.auth_service import AuthError

OAUTH_STATE_PREFIX = "oauth_state:"
MERGE_TOKEN_PREFIX = "merge_token:"
OAUTH_STATE_TTL_SECONDS = 600


def _oauth_state_key(state: str) -> str:
    return f"{OAUTH_STATE_PREFIX}{state}"


def _merge_token_key(token: str) -> str:
    return f"{MERGE_TOKEN_PREFIX}{token}"


def _provider_from_name(name: str) -> AuthProvider:
    try:
        return AuthProvider(name)
    except ValueError as exc:
        raise AuthError("Unsupported OAuth provider.", 400) from exc


def start_oauth_flow(
    provider_name: str,
    settings: Settings,
    *,
    mode: str,
    link_user_id: UUID | None,
    consent: bool,
) -> str:
    provider = _provider_from_name(provider_name)
    if mode == "login" and not consent:
        raise AuthError("Для входа необходимо принять условия обработки персональных данных.", 400)
    if mode == "link" and link_user_id is None:
        raise AuthError("Link mode requires authenticated user.", 400)

    if provider == AuthProvider.vk and not settings.vk_oauth_client_id:
        raise AuthError("VK OAuth is not configured.", 503)
    if provider == AuthProvider.yandex and not settings.yandex_oauth_client_id:
        raise AuthError("Yandex OAuth is not configured.", 503)

    state = generate_token(32)
    payload = {
        "provider": provider.value,
        "mode": mode,
        "link_user_id": str(link_user_id) if link_user_id else None,
        "consent": consent,
    }
    if provider == AuthProvider.vk:
        payload["code_verifier"] = vk_provider.generate_code_verifier()
    get_redis_client().setex(_oauth_state_key(state), OAUTH_STATE_TTL_SECONDS, json.dumps(payload))

    if provider == AuthProvider.vk:
        return vk_provider.vk_authorize_url(settings, state, str(payload["code_verifier"]))
    return yandex_provider.yandex_authorize_url(settings, state)


def _load_oauth_state(state: str) -> dict[str, object]:
    raw = get_redis_client().getdel(_oauth_state_key(state))
    if raw is None:
        raise AuthError("OAuth state expired or invalid.", 400)
    return json.loads(raw)


def _exchange_profile(
    provider: AuthProvider,
    settings: Settings,
    code: str,
    *,
    state: str,
    state_payload: dict[str, object],
    device_id: str | None,
) -> OAuthProfile:
    if provider == AuthProvider.vk:
        code_verifier = state_payload.get("code_verifier")
        if not isinstance(code_verifier, str) or not code_verifier:
            raise AuthError("VK OAuth session is missing PKCE verifier.", 400)
        if not device_id:
            raise AuthError("VK OAuth callback is missing device_id.", 400)
        return vk_provider.vk_exchange_code(
            settings,
            code,
            code_verifier=code_verifier,
            device_id=device_id,
            state=state,
        )
    if provider == AuthProvider.yandex:
        return yandex_provider.yandex_exchange_code(settings, code)
    raise AuthError("Unsupported OAuth provider.", 400)


def handle_oauth_callback(
    db: Session,
    settings: Settings,
    provider_name: str,
    code: str,
    state: str,
    *,
    device_id: str | None = None,
    signup_context: SignupContext | None = None,
) -> tuple[UUID, str | None, str]:
    provider = _provider_from_name(provider_name)
    state_payload = _load_oauth_state(state)
    if state_payload.get("provider") != provider.value:
        raise AuthError("OAuth provider mismatch.", 400)

    try:
        profile = _exchange_profile(
            provider,
            settings,
            code,
            state=state,
            state_payload=state_payload,
            device_id=device_id,
        )
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(f"OAuth exchange failed: {exc}", 502) from exc

    mode = str(state_payload.get("mode") or "login")
    link_user_id_raw = state_payload.get("link_user_id")
    consent = bool(state_payload.get("consent"))

    existing = find_identity(db, provider, profile.external_id)
    if mode == "link":
        if link_user_id_raw is None:
            raise AuthError("Link mode requires authenticated user.", 400)
        survivor_id = UUID(str(link_user_id_raw))
        survivor = db.query(User).filter(User.id == survivor_id).one_or_none()
        if survivor is None:
            raise AuthError("User not found.", 404)
        if existing is not None and existing.user_id != survivor.id:
            merged = db.query(User).filter(User.id == existing.user_id).one_or_none()
            if merged is not None and not list_user_platform_links(db, merged.id):
                orphan_identities = list_user_identities(db, merged.id)
                if len(orphan_identities) == 1 and orphan_identities[0].id == existing.id:
                    upsert_oauth_identity(db, survivor, provider, profile)
                    delete_user_with_dependencies(db, merged, reassign_sync_jobs_to=survivor.id)
                    survivor.last_login_at = datetime.now(timezone.utc)
                    db.commit()
                    return survivor.id, None, "settings"
            if merged is None:
                raise AuthError("Linked account not found.", 404)
            merge_token = _store_merge_token(db, survivor, merged)
            return survivor.id, merge_token, "settings"
        upsert_oauth_identity(db, survivor, provider, profile)
        survivor.last_login_at = datetime.now(timezone.utc)
        db.commit()
        return survivor.id, None, "settings"

    created_new = False
    twin = None if existing is not None else _user_by_verified_email(db, profile)
    if existing is not None:
        user = existing.user
        upsert_oauth_identity(db, user, provider, profile)
    elif twin is not None:
        # Тем же ящиком уже подтверждён профиль (вход по коду на почту или
        # другой провайдер, отдавший тот же адрес). Это один человек — добавляем
        # ему способ входа вместо второго аккаунта.
        user = twin
        upsert_oauth_identity(db, user, provider, profile)
    else:
        # Лимит стоит только здесь: вход существующим профилем свободен, а
        # рождение нового считаем по адресу и устройству.
        if signup_context is not None:
            decision = check_signup_allowed(signup_context, settings)
            if not decision.allowed:
                record_block(signup_context, decision, provider=provider.value)
                raise AuthError(signup_block_message(decision), 429)
        user = create_oauth_user(db, profile, provider, consent=consent)
        created_new = True
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    # Счётчик двигаем после коммита: не состоявшаяся регистрация не должна
    # занимать место в суточном лимите.
    if created_new and signup_context is not None:
        register_signup(signup_context, settings)

    from app.services.onboarding_service import post_login_redirect_target

    return user.id, None, post_login_redirect_target(db, user.id)


def _user_by_verified_email(db: Session, profile: OAuthProfile) -> User | None:
    """Профиль, у которого уже подтверждён тот же почтовый ящик.

    Провайдер отдаёт адрес только после того, как человек залогинился у него,
    то есть владение ящиком доказано — как и кодом из нашего письма. Значит
    второй аккаунт на тот же ящик заводить не нужно: это тот же человек,
    вернувшийся другим способом.

    VK адреса не отдаёт вовсе, так что для него склейка невозможна — там
    объединение остаётся ручным, через «Способы входа» в настройках.
    """
    if not profile.email:
        return None

    # Локальный импорт: email_auth_service тянет мейлер и celery, а oauth_service
    # грузится на старте приложения.
    from app.services.email_auth_service import find_identity_by_mailbox

    normalized = email_address.normalize(profile.email)
    identity = find_identity_by_mailbox(db, normalized)
    return identity.user if identity is not None else None


def _store_merge_token(db: Session, survivor: User, merged: User) -> str:
    preview = merge_preview_payload(db, survivor, merged)
    token = generate_token(24)
    payload = {
        "survivor_user_id": str(survivor.id),
        "merged_user_id": str(merged.id),
        "preview": preview,
    }
    get_redis_client().setex(_merge_token_key(token), OAUTH_STATE_TTL_SECONDS, json.dumps(payload, default=str))
    return token


def store_merge_token_for_users(db: Session, survivor: User, merged: User) -> str:
    return _store_merge_token(db, survivor, merged)


def get_merge_preview_by_token(db: Session, token: str, *, survivor_user_id: UUID | None = None) -> dict[str, object]:
    raw = get_redis_client().get(_merge_token_key(token))
    if raw is None:
        raise AuthError("Merge token expired or invalid.", 404)
    payload = json.loads(raw)
    survivor_id = UUID(str(payload["survivor_user_id"]))
    merged_id = UUID(str(payload["merged_user_id"]))
    if survivor_user_id is not None and survivor_id != survivor_user_id:
        raise AuthError("Merge token does not belong to current user.", 403)
    preview = build_merge_preview(db, survivor_id, merged_id)
    preview["merge_token"] = token
    return preview


def confirm_merge(db: Session, token: str, *, survivor_user_id: UUID | None = None) -> UUID:
    raw = get_redis_client().getdel(_merge_token_key(token))
    if raw is None:
        raise AuthError("Merge token expired or invalid.", 404)
    payload = json.loads(raw)
    survivor_id = UUID(str(payload["survivor_user_id"]))
    merged_id = UUID(str(payload["merged_user_id"]))
    if survivor_user_id is not None and survivor_id != survivor_user_id:
        raise AuthError("Merge token does not belong to current user.", 403)
    try:
        survivor = merge_users(db, survivor_id, merged_id)
    except AccountMergeError as exc:
        raise AuthError(exc.message, exc.status_code) from exc
    return survivor.id
