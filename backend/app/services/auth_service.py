from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.abuse_store import is_telegram_banned
from app.core.admin import is_admin_telegram_id
from app.core.rate_limit import check_rate_limit
from app.core.redis_client import get_redis_client
from app.core.security import generate_token, hash_token
from app.core.session import create_session
from app.core.site_stats import record_login_request
from app.models import (
    AuthLoginRequest,
    AuthLoginRequestStatus,
    AuthOneTimeToken,
    User,
)
from app.services.auth_identity_service import find_user_by_telegram_id, upsert_telegram_identity

LOGIN_REQUEST_REDIS_PREFIX = "login_req:"
LOGIN_REQUEST_LINK_PREFIX = "login_req_link:"
MERGE_REQUIRED_PREFIX = "login_req_merge:"
MAGIC_TOKEN_REDIS_PREFIX = "magic_token:"


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _login_request_redis_key(token: str) -> str:
    return f"{LOGIN_REQUEST_REDIS_PREFIX}{token}"


def _magic_token_redis_key(token_hash: str) -> str:
    return f"{MAGIC_TOKEN_REDIS_PREFIX}{token_hash}"


def _login_request_link_redis_key(token: str) -> str:
    return f"{LOGIN_REQUEST_LINK_PREFIX}{token}"


def _login_request_merge_redis_key(token: str) -> str:
    return f"{MERGE_REQUIRED_PREFIX}{token}"


def create_login_request(
    db: Session,
    settings: Settings,
    client_ip: str,
    *,
    link_user_id: UUID | None = None,
) -> dict[str, object]:
    rate_key = f"auth:login:ip:{client_ip}"
    if not check_rate_limit(rate_key, settings.auth_rate_limit_login_per_ip, settings.auth_rate_limit_login_window_seconds):
        raise AuthError("Too many login attempts. Try again later.", 429)

    request_token = generate_token(24)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.login_request_ttl_seconds)

    login_request = AuthLoginRequest(
        request_token=request_token,
        status=AuthLoginRequestStatus.pending,
        expires_at=expires_at,
    )
    db.add(login_request)
    db.commit()
    record_login_request()

    redis_client = get_redis_client()
    redis_client.setex(
        _login_request_redis_key(request_token),
        settings.login_request_ttl_seconds,
        "pending",
    )
    if link_user_id is not None:
        redis_client.setex(
            _login_request_link_redis_key(request_token),
            settings.login_request_ttl_seconds,
            str(link_user_id),
        )

    bot_username = settings.telegram_bot_username.lstrip("@")
    if not bot_username:
        raise AuthError("Telegram bot username is not configured.", 503)
    bot_url = f"https://t.me/{bot_username}?start=login_{request_token}"

    return {
        "request_token": request_token,
        "bot_url": bot_url,
        "expires_in": settings.login_request_ttl_seconds,
    }


def _rehydrate_login_request_redis(
    redis_client,
    login_request: AuthLoginRequest,
) -> str:
    redis_key = _login_request_redis_key(login_request.request_token)
    ttl_seconds = int(
        (_ensure_utc(login_request.expires_at) - datetime.now(timezone.utc)).total_seconds()
    )
    if ttl_seconds > 0:
        redis_client.setex(redis_key, ttl_seconds, "pending")
    return redis_key


def get_login_request_status(db: Session, request_token: str) -> dict[str, str | None]:
    redis_client = get_redis_client()
    merge_token = redis_client.get(_login_request_merge_redis_key(request_token))
    if merge_token is not None:
        return {"status": "merge_required", "merge_token": merge_token}
    status_value = redis_client.get(_login_request_redis_key(request_token))
    if status_value is not None:
        return {"status": status_value, "merge_token": None}

    login_request = (
        db.query(AuthLoginRequest).filter(AuthLoginRequest.request_token == request_token).one_or_none()
    )
    if (
        login_request is not None
        and login_request.status == AuthLoginRequestStatus.pending
        and _ensure_utc(login_request.expires_at) >= datetime.now(timezone.utc)
    ):
        _rehydrate_login_request_redis(redis_client, login_request)
        return {"status": "pending", "merge_token": None}

    return {"status": "expired", "merge_token": None}


def _telegram_default_display_name(
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


def bot_confirm_login(
    db: Session,
    settings: Settings,
    request_token: str,
    telegram_id: int,
    telegram_username: str | None,
    telegram_chat_id: int,
    telegram_first_name: str | None = None,
    telegram_last_name: str | None = None,
    *,
    consent_accepted: bool = False,
) -> str | None:
    rate_key = f"auth:magic:tg:{telegram_id}"
    if not check_rate_limit(
        rate_key,
        settings.auth_rate_limit_magic_per_telegram,
        settings.auth_rate_limit_magic_window_seconds,
    ):
        raise AuthError("Too many auth attempts for this Telegram account.", 429)

    if is_telegram_banned(telegram_id) and not is_admin_telegram_id(telegram_id, settings):
        raise AuthError("Account temporarily suspended.", 403)

    redis_client = get_redis_client()
    redis_key = _login_request_redis_key(request_token)
    current_status = redis_client.get(redis_key)

    login_request = db.query(AuthLoginRequest).filter(AuthLoginRequest.request_token == request_token).one_or_none()
    if login_request is None or login_request.status != AuthLoginRequestStatus.pending:
        raise AuthError("Login request expired or not found.", 404)

    if _ensure_utc(login_request.expires_at) < datetime.now(timezone.utc):
        login_request.status = AuthLoginRequestStatus.expired
        redis_client.delete(redis_key)
        db.commit()
        raise AuthError("Login request expired.", 410)

    if current_status is None:
        redis_key = _rehydrate_login_request_redis(redis_client, login_request)
        current_status = "pending"
    elif current_status != "pending":
        raise AuthError("Login request already used.", 409)

    default_name = _telegram_default_display_name(
        first_name=telegram_first_name,
        last_name=telegram_last_name,
        username=telegram_username,
    )

    link_user_id_raw = redis_client.get(_login_request_link_redis_key(request_token))
    link_user_id = UUID(link_user_id_raw) if link_user_id_raw else None

    existing_user = find_user_by_telegram_id(db, telegram_id)
    needs_consent = existing_user is None or not existing_user.consent_accepted
    if needs_consent and not consent_accepted:
        raise AuthError(
            "Для входа необходимо принять условия обработки персональных данных.",
            400,
        )

    now = datetime.now(timezone.utc)

    if link_user_id is not None:
        survivor = db.query(User).filter(User.id == link_user_id).one_or_none()
        if survivor is None:
            raise AuthError("User not found.", 404)
        if existing_user is not None and existing_user.id != survivor.id:
            from app.services.oauth_service import store_merge_token_for_users

            merge_token = store_merge_token_for_users(db, survivor, existing_user)
            redis_client.setex(redis_key, settings.magic_link_ttl_seconds, "merge_required")
            redis_client.setex(
                _login_request_merge_redis_key(request_token),
                settings.magic_link_ttl_seconds,
                merge_token,
            )
            login_request.status = AuthLoginRequestStatus.completed
            login_request.telegram_id = telegram_id
            db.commit()
            return ""
        user = survivor
    elif existing_user is not None:
        user = existing_user
    else:
        user = User(
            display_name=default_name,
            consent_accepted=consent_accepted,
            consent_ts=now if consent_accepted else None,
        )
        db.add(user)
        db.flush()

    if consent_accepted and not user.consent_accepted:
        user.consent_accepted = True
        user.consent_ts = now

    upsert_telegram_identity(
        db,
        user,
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        telegram_first_name=telegram_first_name,
        telegram_last_name=telegram_last_name,
        telegram_chat_id=telegram_chat_id,
    )

    if link_user_id is not None:
        login_request.status = AuthLoginRequestStatus.completed
        login_request.telegram_id = telegram_id
        db.commit()
        redis_client.setex(redis_key, settings.magic_link_ttl_seconds, "linked")
        return ""

    raw_magic_token = generate_token(32)
    token_hash = hash_token(raw_magic_token)
    magic_expires = datetime.now(timezone.utc) + timedelta(seconds=settings.magic_link_ttl_seconds)

    magic_record = AuthOneTimeToken(
        token_hash=token_hash,
        user_id=user.id,
        login_request_id=login_request.id,
        expires_at=magic_expires,
    )
    db.add(magic_record)

    login_request.status = AuthLoginRequestStatus.completed
    login_request.telegram_id = telegram_id
    db.commit()

    redis_client.setex(redis_key, settings.magic_link_ttl_seconds, "link_sent")
    redis_client.setex(
        _magic_token_redis_key(token_hash),
        settings.magic_link_ttl_seconds,
        str(user.id),
    )

    return f"{settings.app_base_url.rstrip('/')}/api/auth/callback?token={raw_magic_token}"


def consume_magic_link(db: Session, settings: Settings, raw_token: str) -> UUID:
    token_hash = hash_token(raw_token)
    redis_client = get_redis_client()
    redis_key = _magic_token_redis_key(token_hash)
    used_key = f"{redis_key}:used"

    user_id_raw = redis_client.getdel(redis_key)
    if user_id_raw is None:
        if redis_client.exists(used_key):
            raise AuthError("Magic link already used.", 409)
        raise AuthError("Magic link expired or invalid.", 404)

    redis_client.setex(used_key, settings.magic_link_ttl_seconds, "1")

    magic_record = (
        db.query(AuthOneTimeToken)
        .filter(AuthOneTimeToken.token_hash == token_hash, AuthOneTimeToken.used_at.is_(None))
        .one_or_none()
    )
    if magic_record is None:
        raise AuthError("Magic link expired or invalid.", 404)

    if _ensure_utc(magic_record.expires_at) < datetime.now(timezone.utc):
        raise AuthError("Magic link expired.", 410)

    now = datetime.now(timezone.utc)
    magic_record.used_at = now
    user = db.query(User).filter(User.id == magic_record.user_id).one()
    user.last_login_at = now
    db.commit()

    return user.id


def create_user_session(settings: Settings, user_id: UUID) -> str:
    return create_session(settings, str(user_id))


def update_user_display_name(db: Session, user: User, display_name: str) -> User:
    cleaned = display_name.strip()
    if not cleaned:
        user.display_name_customized = False
        user.display_name = _telegram_default_display_name(
            first_name=user.telegram_first_name,
            last_name=user.telegram_last_name,
            username=user.telegram_username,
        )
    else:
        user.display_name = cleaned
        user.display_name_customized = True
    db.commit()
    db.refresh(user)
    return user
