"""Вход по одноразовому коду на почту.

Почему код, а не ссылка: письмо человек чаще открывает на телефоне, а сайт у
него в этот момент на компьютере — по ссылке сессия заведётся не на том
устройстве. Плюс почтовые антивирусы «прожимают» ссылки в письмах ради
проверки, и одноразовый переход сгорает до того, как до него дойдёт человек.
Шестизначный код переносится глазами и от этого не страдает.

Код в Redis не хранится: лежит его хэш, как у пароля. Подобрать за отведённые
пять попыток из миллиона вариантов нельзя, а утечка дампа Redis не даёт войти.

Аккаунт по почте — такой же, как через VK или Яндекс: идентичность в
auth_identities с provider='email' и нормализованным адресом в external_id.
Если этой почтой уже подтверждён другой профиль (Яндекс отдаёт verified
email), входим в него, а не плодим второй: обе стороны доказали владение одним
ящиком.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.providers.base import OAuthProfile
from app.config import Settings
from app.core import email_address
from app.core.mailer import is_configured as mailer_is_configured
from app.core.rate_limit import check_rate_limit
from app.core.redis_client import get_redis_client
from app.core.security import hash_token
from app.core.signup_guard import (
    SignupContext,
    check_signup_allowed,
    record_block,
    register_signup,
    signup_block_message,
)
from app.models import AuthIdentity, AuthProvider, User
from app.services.auth_identity_service import (
    create_oauth_user,
    find_identity,
    upsert_oauth_identity,
)
from app.services.auth_service import AuthError

logger = logging.getLogger(__name__)

CODE_PREFIX = "auth:email:code:"


def _code_key(normalized_email: str) -> str:
    return f"{CODE_PREFIX}{normalized_email}"


def _generate_code(length: int) -> str:
    # Ведущие нули допустимы: человек вводит ровно то, что видит в письме.
    return "".join(secrets.choice("0123456789") for _ in range(length))


def _candidate_domains(normalized_email: str) -> set[str]:
    """Домены, под которыми тот же ящик мог зарегистрироваться раньше.

    Нужно, чтобы не тащить из базы все почтовые идентичности: сначала грубый
    отбор по домену в SQL, потом точное сравнение нормализацией.
    """
    _, domain = email_address.split(normalized_email)
    if domain == "gmail.com":
        return {"gmail.com", "googlemail.com"}
    if domain == "yandex.ru":
        return {
            "yandex.ru",
            "yandex.com",
            "yandex.by",
            "yandex.kz",
            "yandex.ua",
            "yandex.com.tr",
            "ya.ru",
        }
    return {domain}


def find_identity_by_mailbox(db: Session, normalized_email: str) -> AuthIdentity | None:
    """Найти любую идентичность (хоть Яндекс, хоть почтовую) с тем же ящиком."""
    domains = _candidate_domains(normalized_email)
    query = db.query(AuthIdentity).filter(AuthIdentity.email.isnot(None))
    conditions = [AuthIdentity.email.ilike(f"%@{domain}") for domain in domains]
    if len(conditions) == 1:
        query = query.filter(conditions[0])
    else:
        from sqlalchemy import or_

        query = query.filter(or_(*conditions))

    for identity in query.all():
        if identity.email and email_address.normalize(identity.email) == normalized_email:
            return identity
    return None


def request_code(
    db: Session,
    settings: Settings,
    raw_email: str,
    *,
    client_ip: str,
    consent: bool,
) -> dict[str, object]:
    """Выслать код на указанный адрес.

    Ответ намеренно одинаков и для нового человека, и для существующего: по
    нему нельзя перебором узнать, зарегистрирован ли кто-то с этой почтой.
    """
    if not settings.email_login_enabled:
        raise AuthError("Вход по почте временно недоступен.", 503)
    if not mailer_is_configured(settings):
        raise AuthError("Отправка писем не настроена.", 503)

    email = raw_email.strip()
    if not email_address.is_valid(email):
        raise AuthError("Проверьте адрес почты.", 400)
    if email_address.is_disposable(email):
        raise AuthError("Одноразовые почтовые ящики не подходят для входа.", 400)

    normalized = email_address.normalize(email)

    # Два ведра: одно бережёт чужой ящик от того, кто вписывает его адрес
    # ради спама, второе — нас от перебора адресов с одной машины.
    if not check_rate_limit(
        f"auth:email:req:{normalized}",
        settings.email_login_code_per_address,
        settings.email_login_code_window_seconds,
    ):
        raise AuthError("Код уже отправлен. Проверьте почту или попробуйте позже.", 429)
    if not check_rate_limit(
        f"auth:email:req:ip:{client_ip}",
        settings.email_login_code_per_ip,
        settings.email_login_code_window_seconds,
    ):
        raise AuthError("Слишком много запросов. Попробуйте позже.", 429)

    code = _generate_code(settings.email_login_code_length)
    payload = {
        "code_hash": hash_token(code),
        "email": email,
        "consent": consent,
        "attempts": 0,
    }
    get_redis_client().setex(
        _code_key(normalized),
        settings.email_login_code_ttl_seconds,
        json.dumps(payload),
    )

    _send_code(settings, to=email, code=code)
    return {"expires_in": settings.email_login_code_ttl_seconds}


def _send_code(settings: Settings, *, to: str, code: str) -> None:
    minutes = max(1, settings.email_login_code_ttl_seconds // 60)
    subject = f"Код для входа: {code}"
    text_body = (
        f"Код для входа на run5k.run: {code}\n\n"
        f"Он действует {minutes} мин. и подходит только для одного входа.\n\n"
        "Если вы не запрашивали код, просто удалите это письмо — "
        "без него войти в профиль нельзя.\n"
    )
    html_body = (
        f"<p>Код для входа на run5k.run:</p>"
        f'<p style="font-size:28px;font-weight:700;letter-spacing:4px">{code}</p>'
        f"<p>Он действует {minutes} мин. и подходит только для одного входа.</p>"
        "<p>Если вы не запрашивали код, просто удалите это письмо — "
        "без него войти в профиль нельзя.</p>"
    )

    # Локальный импорт: celery тянет за собой брокер, а сервис зовётся из тестов.
    from app.workers.tasks.email_send import queue_email

    if queue_email(to, subject, text_body, html_body):
        return

    # Брокер лежит — шлём сами. Медленнее, но лучше, чем «код не пришёл».
    from app.core.mailer import MailerError, send_email

    try:
        send_email(settings, to=to, subject=subject, text_body=text_body, html_body=html_body)
    except MailerError as exc:
        logger.exception("email login: failed to send code to %s", to)
        raise AuthError("Не удалось отправить письмо. Попробуйте позже.", 502) from exc


def verify_code(
    db: Session,
    settings: Settings,
    raw_email: str,
    code: str,
    *,
    signup_context: SignupContext | None = None,
) -> UUID:
    """Проверить код и вернуть id профиля, создав его при первом входе."""
    normalized, display_email, consent = _consume_code(db, settings, raw_email, code)
    return _login_or_create(
        db,
        settings,
        normalized=normalized,
        display_email=display_email,
        consent=consent,
        signup_context=signup_context,
    )


def _consume_code(
    db: Session,
    settings: Settings,
    raw_email: str,
    code: str,
) -> tuple[str, str, bool]:
    """Сверить код и сжечь его. Возвращает (нормализованный ящик, адрес, согласие)."""
    if not settings.email_login_enabled:
        raise AuthError("Вход по почте временно недоступен.", 503)

    email = raw_email.strip()
    if not email_address.is_valid(email):
        raise AuthError("Проверьте адрес почты.", 400)

    normalized = email_address.normalize(email)
    redis_client = get_redis_client()
    raw_state = redis_client.get(_code_key(normalized))
    if raw_state is None:
        raise AuthError("Код истёк или уже использован. Запросите новый.", 400)

    state = json.loads(raw_state)
    attempts = int(state.get("attempts", 0))
    if attempts >= settings.email_login_max_attempts:
        redis_client.delete(_code_key(normalized))
        raise AuthError("Слишком много попыток. Запросите новый код.", 429)

    if hash_token(code.strip()) != state.get("code_hash"):
        state["attempts"] = attempts + 1
        ttl = redis_client.ttl(_code_key(normalized))
        redis_client.setex(
            _code_key(normalized),
            ttl if ttl and ttl > 0 else settings.email_login_code_ttl_seconds,
            json.dumps(state),
        )
        left = settings.email_login_max_attempts - state["attempts"]
        if left <= 0:
            redis_client.delete(_code_key(normalized))
            raise AuthError("Слишком много попыток. Запросите новый код.", 429)
        raise AuthError("Неверный код. Попробуйте ещё раз.", 400)

    # Код верный — сжигаем его сразу: он одноразовый.
    redis_client.delete(_code_key(normalized))

    consent = bool(state.get("consent"))
    display_email = str(state.get("email") or email)
    return normalized, display_email, consent


def link_email(
    db: Session,
    settings: Settings,
    user: User,
    raw_email: str,
    code: str,
) -> str | None:
    """Привязать почту к профилю, в котором человек уже сидит.

    Возвращает merge-токен, если этим ящиком владеет другой профиль: тогда
    решение об объединении принимает человек — так же, как при привязке VK или
    Яндекса. None означает «привязали, делать больше нечего».
    """
    normalized, display_email, _consent = _consume_code(db, settings, raw_email, code)
    profile = _profile_for(normalized, display_email)

    existing = find_identity(db, AuthProvider.email, normalized)
    if existing is not None and existing.user_id != user.id:
        # Локальный импорт: oauth_service сам зовёт нас за поиском по ящику.
        from app.services.oauth_service import store_merge_token_for_users

        other = db.query(User).filter(User.id == existing.user_id).one_or_none()
        if other is None:
            raise AuthError("Профиль с этой почтой не найден.", 404)
        return store_merge_token_for_users(db, user, other)

    upsert_oauth_identity(db, user, AuthProvider.email, profile)
    db.commit()
    return None


def _profile_for(normalized: str, display_email: str) -> OAuthProfile:
    return OAuthProfile(
        external_id=normalized,
        display_name=display_email.split("@", 1)[0],
        email=display_email,
        profile_json={"normalized_email": normalized},
    )


def _login_or_create(
    db: Session,
    settings: Settings,
    *,
    normalized: str,
    display_email: str,
    consent: bool,
    signup_context: SignupContext | None,
) -> UUID:
    profile = _profile_for(normalized, display_email)

    existing = find_identity(db, AuthProvider.email, normalized)
    if existing is not None:
        user = existing.user
        upsert_oauth_identity(db, user, AuthProvider.email, profile)
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        return user.id

    # Этой почтой мог быть подтверждён профиль через Яндекс: тогда это тот же
    # человек, и второй аккаунт ему не нужен — просто добавляем способ входа.
    twin = find_identity_by_mailbox(db, normalized)
    if twin is not None:
        user = twin.user
        upsert_oauth_identity(db, user, AuthProvider.email, profile)
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("email login: linked mailbox %s to existing user %s", normalized, user.id)
        return user.id

    if signup_context is not None:
        decision = check_signup_allowed(signup_context, settings)
        if not decision.allowed:
            record_block(signup_context, decision, provider=AuthProvider.email.value)
            raise AuthError(signup_block_message(decision), 429)

    user: User = create_oauth_user(db, profile, AuthProvider.email, consent=consent)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    if signup_context is not None:
        register_signup(signup_context, settings)
    return user.id
