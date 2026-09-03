from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.abuse_store import is_telegram_banned
from app.core.admin import is_admin_telegram_id
from app.core.rate_limit import check_rate_limit
from app.core.redis_client import get_redis_client
from app.core.security import generate_token, hash_token
from app.core.session import create_session
from app.core.site_stats import record_login_request
from app.core.user_agent import describe_user_agent
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
# Откуда пришёл запрос (IP, User-Agent, согласие, время) — бот показывает это
# человеку перед «Подтвердить вход», чтобы чужой запрос не подтвердили вслепую.
LOGIN_REQUEST_CONTEXT_PREFIX = "login_req_ctx:"
# Подтверждённый в боте вход, который вкладка сайта ещё не забрала: user_id
# под токеном запроса. Живёт столько же, сколько ссылка-страховка в боте.
LOGIN_REQUEST_CLAIM_PREFIX = "login_req_claim:"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


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


def _login_request_context_redis_key(token: str) -> str:
    return f"{LOGIN_REQUEST_CONTEXT_PREFIX}{token}"


def _login_request_claim_redis_key(token: str) -> str:
    return f"{LOGIN_REQUEST_CLAIM_PREFIX}{token}"


def create_login_request(
    db: Session,
    settings: Settings,
    client_ip: str,
    *,
    link_user_id: UUID | None = None,
    user_agent: str = "",
    consent: bool = False,
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
    redis_client.setex(
        _login_request_context_redis_key(request_token),
        settings.login_request_ttl_seconds,
        json.dumps(
            {
                "ip": client_ip[:64],
                "user_agent": user_agent[:256],
                "consent": bool(consent),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
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


def _requested_at_label(created_at_raw: str | None) -> str:
    if not created_at_raw:
        return ""
    try:
        created_at = _ensure_utc(datetime.fromisoformat(created_at_raw))
    except ValueError:
        return ""
    local = created_at.astimezone(MOSCOW_TZ)
    today = datetime.now(MOSCOW_TZ).date()
    if local.date() == today:
        return f"сегодня в {local:%H:%M} (МСК)"
    return f"{local:%d.%m.%Y в %H:%M} (МСК)"


def get_login_request_context(
    db: Session,
    settings: Settings,
    request_token: str,
    telegram_id: int,
) -> dict[str, object]:
    """Что бот показывает перед кнопкой «Подтвердить вход».

    Статус берём тем же путём, что и вкладка сайта; остальное — из контекста,
    записанного при создании запроса. Город ищем здесь, а не при создании:
    внешний сервис дёргается только когда до бота вообще дошли.
    """
    status = get_login_request_status(db, request_token)["status"]
    redis_client = get_redis_client()
    raw_context = redis_client.get(_login_request_context_redis_key(request_token))
    context = json.loads(raw_context) if raw_context else {}

    existing_user = find_user_by_telegram_id(db, telegram_id)
    needs_consent = existing_user is None or not existing_user.consent_accepted

    ua = describe_user_agent(str(context.get("user_agent") or ""))
    city = ""
    if status == "pending":
        # Локальный импорт: сервис тянет httpx и настройки, нужен только здесь.
        from app.services.ip_geo_service import city_for_ip

        city = city_for_ip(str(context.get("ip") or ""), settings)

    return {
        "status": status,
        "needs_consent": needs_consent,
        "consent_given": bool(context.get("consent")),
        "link_mode": redis_client.exists(_login_request_link_redis_key(request_token)) > 0,
        "browser": ua.browser,
        "os": ua.os,
        "city": city,
        "requested_at_label": _requested_at_label(context.get("created_at")),
    }


def bot_deny_login(db: Session, settings: Settings, request_token: str) -> None:
    """«Это не я»: запрос гасим, вкладка сайта увидит denied и объяснит, что случилось."""
    redis_client = get_redis_client()
    login_request = db.query(AuthLoginRequest).filter(AuthLoginRequest.request_token == request_token).one_or_none()
    if login_request is not None and login_request.status == AuthLoginRequestStatus.pending:
        login_request.status = AuthLoginRequestStatus.expired
        db.commit()
    redis_client.setex(_login_request_redis_key(request_token), settings.login_request_ttl_seconds, "denied")
    redis_client.delete(_login_request_context_redis_key(request_token))
    redis_client.delete(_login_request_link_redis_key(request_token))


def claim_login_request(db: Session, settings: Settings, request_token: str) -> UUID:
    """Вкладка сайта забирает подтверждённый в боте вход.

    Токен запроса знает только та вкладка, что его создала (и Telegram, куда
    его унёс сам человек), поэтому предъявить его — значит быть той вкладкой.
    Забрать можно один раз: второй claim упрётся в 409.
    """
    redis_client = get_redis_client()
    user_id_raw = redis_client.getdel(_login_request_claim_redis_key(request_token))
    if user_id_raw is None:
        status = redis_client.get(_login_request_redis_key(request_token))
        if status == "claimed":
            raise AuthError("Вход уже выполнен в этой вкладке.", 409)
        raise AuthError("Подтверждение не найдено или истекло.", 404)

    redis_client.setex(_login_request_redis_key(request_token), settings.magic_link_ttl_seconds, "claimed")
    user = db.query(User).filter(User.id == UUID(user_id_raw)).one_or_none()
    if user is None:
        raise AuthError("Профиль не найден.", 404)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user.id


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

    # Галка на странице входа — такое же согласие, как кнопка в боте, и она
    # записана в контексте запроса. Учитывать только флаг бота было нельзя:
    # бот показывает кнопку с текстом согласия ТОЛЬКО когда галки на сайте не
    # было, а иначе шлёт consent_accepted=false — и человек, поставивший галку,
    # получал «необходимо принять условия», которые он только что принял
    # (жалоба пользователя 03.09.2026).
    raw_context = redis_client.get(_login_request_context_redis_key(request_token))
    consent_on_site = bool(json.loads(raw_context).get("consent")) if raw_context else False
    consent_accepted = consent_accepted or consent_on_site

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
            redis_client.delete(_login_request_context_redis_key(request_token))
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
        redis_client.delete(_login_request_context_redis_key(request_token))
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

    # Вкладка сайта ждёт именно этого: заберёт сессию сама по токену запроса.
    # Ссылка в боте остаётся страховкой на случай, если вкладку закрыли.
    redis_client.setex(redis_key, settings.magic_link_ttl_seconds, "confirmed")
    redis_client.setex(
        _login_request_claim_redis_key(request_token),
        settings.magic_link_ttl_seconds,
        str(user.id),
    )
    redis_client.delete(_login_request_context_redis_key(request_token))
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


