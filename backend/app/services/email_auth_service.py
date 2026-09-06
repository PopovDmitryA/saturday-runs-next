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
from app.core.email_templates import login_code_email
from app.core.mailer import is_configured as mailer_is_configured
from app.core.mailer import sender_email
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
from app.services.email_login_journal_service import (
    PURPOSE_LOGIN,
    discard_request,
    mark_failed_attempt,
    mark_verified,
    record_request,
)

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
    news_consent: bool = False,
    purpose: str = PURPOSE_LOGIN,
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
    # Общий потолок на сутки: ведра по адресу и по IP держат одного обидчика,
    # но не толпу. Упёрлись — вход по почте молчит до завтра, а VK и Яндекс
    # работают; это лучше, чем выжечь суточный лимит почтового кластера и
    # остаться вообще без исходящей почты.
    if not check_rate_limit("auth:email:req:global", settings.email_login_codes_per_day, 86400):
        logger.error("email login: daily code quota exhausted (%s)", settings.email_login_codes_per_day)
        raise AuthError("Вход по почте временно недоступен. Попробуйте войти через VK или Яндекс.", 503)

    code = _generate_code(settings.email_login_code_length)
    # Строку журнала заводим до отправки: её id уходит в Redis вместе с кодом,
    # чтобы потом отметить verified именно то письмо, по которому вошли.
    # Не ушло — строку убираем, см. except ниже.
    request_id = record_request(
        db,
        normalized_email=normalized,
        purpose=purpose,
        known_mailbox=find_identity_by_mailbox(db, normalized) is not None,
        ip=client_ip,
    )
    _remember_code(
        settings,
        normalized=normalized,
        code=code,
        email=email,
        consent=consent,
        news_consent=news_consent,
        request_id=request_id,
    )

    # Подписку предлагаем только тем, кто ещё не подписан: подписанному это
    # шум, а кнопка «отписаться» служебному письму про вход не место — она
    # живёт в самих рассылках.
    from app.services.newsletter_service import is_subscribed, subscribe_url

    offer_url = None
    if not news_consent and not is_subscribed(db, email):
        offer_url = subscribe_url(settings, normalized)

    try:
        _send_code(settings, to=email, code=code, subscribe_url=offer_url)
    except AuthError:
        discard_request(db, request_id)
        raise
    return {
        "expires_in": settings.email_login_code_ttl_seconds,
        "sender": sender_email(settings),
    }


def _remember_code(
    settings: Settings,
    *,
    normalized: str,
    code: str,
    email: str,
    consent: bool,
    news_consent: bool,
    request_id: int | None = None,
) -> None:
    """Добавить код к действующим для этого ящика.

    Новый код не отменяет предыдущие: почта иногда приходит с задержкой, и
    человек, запросивший код дважды, чаще берёт цифры из письма, которое
    открыл первым. Отвергать его — злить того, кто всё сделал правильно.
    Безопасность от этого не страдает: каждый код всё так же одноразовый,
    живёт свои десять минут, а счётчик попыток общий на ящик.
    """
    redis_client = get_redis_client()
    now = datetime.now(timezone.utc).timestamp()
    state = _load_state(redis_client, normalized)

    codes = [item for item in state.get("codes", []) if float(item.get("exp", 0)) > now]
    codes.append(
        {
            "hash": hash_token(code),
            "exp": now + settings.email_login_code_ttl_seconds,
            "email": email,
            "consent": consent,
            "news": news_consent,
            "req": request_id,
        }
    )
    # Держим только последние: пачка «живых» ключей от профиля ни к чему.
    state["codes"] = codes[-settings.email_login_active_codes :]
    state.setdefault("attempts", 0)

    ttl = max(int(max(float(item["exp"]) for item in state["codes"]) - now), 1)
    redis_client.setex(_code_key(normalized), ttl, json.dumps(state))


def _load_state(redis_client, normalized: str) -> dict[str, object]:
    raw = redis_client.get(_code_key(normalized))
    if not raw:
        return {"codes": [], "attempts": 0}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"codes": [], "attempts": 0}


def _send_code(settings: Settings, *, to: str, code: str, subscribe_url: str | None = None) -> None:
    minutes = max(1, settings.email_login_code_ttl_seconds // 60)
    subject = f"Код для входа: {code}"
    text_body, html_body = login_code_email(code, minutes=minutes, subscribe_url=subscribe_url)

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
    normalized, display_email, consent, news_consent = _consume_code(db, settings, raw_email, code)
    return _login_or_create(
        db,
        settings,
        normalized=normalized,
        display_email=display_email,
        consent=consent,
        news_consent=news_consent,
        signup_context=signup_context,
    )


def _consume_code(
    db: Session,
    settings: Settings,
    raw_email: str,
    code: str,
) -> tuple[str, str, bool, bool]:
    """Сверить код и сжечь его.

    Возвращает (нормализованный ящик, адрес как ввели, согласие на обработку,
    согласие на рассылку).
    """
    if not settings.email_login_enabled:
        raise AuthError("Вход по почте временно недоступен.", 503)

    email = raw_email.strip()
    if not email_address.is_valid(email):
        raise AuthError("Проверьте адрес почты.", 400)

    normalized = email_address.normalize(email)
    redis_client = get_redis_client()
    state = _load_state(redis_client, normalized)
    now = datetime.now(timezone.utc).timestamp()
    codes = [item for item in state.get("codes", []) if float(item.get("exp", 0)) > now]
    if not codes:
        raise AuthError("Код истёк или уже использован. Запросите новый.", 400)

    attempts = int(state.get("attempts", 0))
    if attempts >= settings.email_login_max_attempts:
        redis_client.delete(_code_key(normalized))
        raise AuthError("Слишком много попыток. Запросите новый код.", 429)

    given = hash_token(code.strip())
    matched = next((item for item in codes if item.get("hash") == given), None)
    if matched is None:
        # Неверный код — письмо человек всё-таки увидел. В отчёте по доставке
        # это принципиально другая строка, чем «письмо не открыли вовсе».
        mark_failed_attempt(db, normalized)
        state["codes"] = codes
        state["attempts"] = attempts + 1
        ttl = max(int(max(float(item["exp"]) for item in codes) - now), 1)
        redis_client.setex(_code_key(normalized), ttl, json.dumps(state))
        if state["attempts"] >= settings.email_login_max_attempts:
            redis_client.delete(_code_key(normalized))
            raise AuthError("Слишком много попыток. Запросите новый код.", 429)
        raise AuthError("Неверный код. Попробуйте ещё раз.", 400)

    # Код подошёл — гасим все выданные на этот ящик: вход состоялся.
    redis_client.delete(_code_key(normalized))

    # Письмо, по которому вошли, отмечаем сразу здесь: код сработал — значит
    # человек его получил и нашёл. Всё, что мешает дальше (лимит регистраций),
    # к доставке отношения не имеет и воронку доставки искажать не должно.
    mark_verified(db, matched.get("req"))

    consent = bool(matched.get("consent"))
    display_email = str(matched.get("email") or email)
    return normalized, display_email, consent, bool(matched.get("news"))


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
    normalized, display_email, _consent, news_consent = _consume_code(db, settings, raw_email, code)
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
    _apply_news_consent(user, news_consent)
    db.commit()
    return None


def _apply_news_consent(user: User, news_consent: bool) -> None:
    """Включить рассылку, если человек её отметил.

    Только включаем: снятая галочка на экране входа означает «не отмечал
    сейчас», а не «отпишите меня». Отписка живёт в настройках профиля —
    иначе человек молча терял бы подписку каждым входом.
    """
    if news_consent:
        user.news_subscribed = True


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
    news_consent: bool,
    signup_context: SignupContext | None,
) -> UUID:
    profile = _profile_for(normalized, display_email)

    existing = find_identity(db, AuthProvider.email, normalized)
    if existing is not None:
        user = existing.user
        upsert_oauth_identity(db, user, AuthProvider.email, profile)
        _apply_news_consent(user, news_consent)
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        return user.id

    # Этой почтой мог быть подтверждён профиль через Яндекс: тогда это тот же
    # человек, и второй аккаунт ему не нужен — просто добавляем способ входа.
    twin = find_identity_by_mailbox(db, normalized)
    if twin is not None:
        user = twin.user
        upsert_oauth_identity(db, user, AuthProvider.email, profile)
        _apply_news_consent(user, news_consent)
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
    _apply_news_consent(user, news_consent)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    if signup_context is not None:
        register_signup(signup_context, settings)
    return user.id
