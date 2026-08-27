"""Вход по данным Telegram Login Widget.

Тонкий слой между проверкой подписи (auth/providers/telegram_widget.py) и
профилем: найти существующего человека по telegram_id, иначе завести нового —
как это делают VK и Яндекс.

Отдельно от email-склейки: Telegram адреса почты не отдаёт, поэтому связать
телеграм с уже существующим профилем автоматически не по чему. Объединение
остаётся ручным, через «Способы входа» в настройках.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.providers.telegram_widget import TelegramWidgetProfile, bot_id, verify
from app.config import Settings
from app.core.signup_guard import (
    SignupContext,
    check_signup_allowed,
    record_block,
    register_signup,
    signup_block_message,
)
from app.models import AuthProvider, User
from app.services.auth_identity_service import find_user_by_telegram_id, upsert_telegram_identity
from app.services.auth_service import AuthError

logger = logging.getLogger(__name__)


def login_bot_token(settings: Settings) -> str:
    return settings.telegram_login_bot_token.strip() or settings.telegram_bot_token.strip()


def login_bot_username(settings: Settings) -> str:
    username = settings.telegram_login_bot_username.strip() or settings.telegram_bot_username.strip()
    return username.lstrip("@")


def is_configured(settings: Settings) -> bool:
    return bool(settings.telegram_login_enabled and login_bot_token(settings))


# Поля, которые кладёт в адрес сам Telegram. Всё остальное (наш state) в
# подпись не входит и должно быть отброшено до проверки — иначе она не сойдётся.
TELEGRAM_FIELDS = frozenset(
    {"id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash"}
)

OAUTH_URL = "https://oauth.telegram.org/auth"


def authorize_url(settings: Settings, *, origin: str, return_to: str) -> str:
    """Куда отправить человека подтверждать вход.

    Redirect-режим вместо всплывающего окна: на телефоне человек уходит в
    приложение Telegram, и браузерная вкладка за это время успевает
    перезагрузиться — JS-колбэк всплывающего окна до нас просто не доживает.
    Здесь же Telegram сам возвращает человека на return_to с подписанными
    параметрами в адресе, и терять нечего.
    """
    from urllib.parse import urlencode

    params = {
        "bot_id": bot_id(login_bot_token(settings)),
        "origin": origin,
        "return_to": return_to,
        "request_access": "write",
        "embed": "0",
    }
    return f"{OAUTH_URL}?{urlencode(params)}"


def telegram_fields(params: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in params.items() if key in TELEGRAM_FIELDS}


def login_with_widget(
    db: Session,
    settings: Settings,
    data: dict[str, str],
    *,
    consent: bool,
    signup_context: SignupContext | None = None,
) -> UUID:
    """Проверить данные виджета и вернуть id профиля."""
    if not is_configured(settings):
        raise AuthError("Вход через Telegram недоступен.", 503)

    try:
        profile = verify(
            data,
            bot_token=login_bot_token(settings),
            max_age_seconds=settings.telegram_login_max_age_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — детали проверки наружу не выносим
        logger.warning("telegram login: rejected payload (%s)", exc)
        raise AuthError("Не удалось подтвердить вход через Telegram.", 400) from exc

    user = find_user_by_telegram_id(db, profile.telegram_id)
    if user is None:
        if not consent:
            raise AuthError(
                "Для входа необходимо принять условия обработки персональных данных.", 400
            )
        user = _create_user(db, settings, profile, signup_context=signup_context)
    else:
        _link(db, user, profile)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user.id


def link_telegram(
    db: Session,
    settings: Settings,
    user_id: UUID,
    data: dict[str, str],
) -> str | None:
    """Привязать Telegram к профилю, в котором человек уже сидит.

    Возвращает merge-токен, если этим телеграмом владеет другой профиль: решение
    об объединении принимает человек, как при привязке VK или Яндекса.
    """
    if not is_configured(settings):
        raise AuthError("Вход через Telegram недоступен.", 503)

    try:
        profile = verify(
            data,
            bot_token=login_bot_token(settings),
            max_age_seconds=settings.telegram_login_max_age_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — детали проверки наружу не выносим
        logger.warning("telegram link: rejected payload (%s)", exc)
        raise AuthError("Не удалось подтвердить Telegram.", 400) from exc

    survivor = db.query(User).filter(User.id == user_id).one_or_none()
    if survivor is None:
        raise AuthError("Профиль не найден.", 404)

    owner = find_user_by_telegram_id(db, profile.telegram_id)
    if owner is not None and owner.id != survivor.id:
        # Локальный импорт: oauth_service тянет провайдеров, а мы — его.
        from app.services.oauth_service import store_merge_token_for_users

        return store_merge_token_for_users(db, survivor, owner)

    _link(db, survivor, profile)
    db.commit()
    return None


def _create_user(
    db: Session,
    settings: Settings,
    profile: TelegramWidgetProfile,
    *,
    signup_context: SignupContext | None,
) -> User:
    if signup_context is not None:
        decision = check_signup_allowed(signup_context, settings)
        if not decision.allowed:
            record_block(signup_context, decision, provider=AuthProvider.telegram.value)
            raise AuthError(signup_block_message(decision), 429)

    now = datetime.now(timezone.utc)
    user = User(consent_accepted=True, consent_ts=now)
    db.add(user)
    db.flush()
    _link(db, user, profile)
    db.flush()
    if signup_context is not None:
        register_signup(signup_context, settings)
    return user


def _link(db: Session, user: User, profile: TelegramWidgetProfile) -> None:
    upsert_telegram_identity(
        db,
        user,
        telegram_id=profile.telegram_id,
        telegram_username=profile.username,
        telegram_first_name=profile.first_name,
        telegram_last_name=profile.last_name,
        # Виджет chat_id не отдаёт: он появится, когда человек напишет боту.
        # Для входа он не нужен, нужен только для уведомлений.
        telegram_chat_id=user.telegram_chat_id,
    )
