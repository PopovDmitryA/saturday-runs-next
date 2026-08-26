"""Подписка на новости по ссылке из письма.

Ссылка должна работать в один клик, из почты, без входа на сайт: человек уже
доказал владение ящиком тем, что письмо до него дошло. Поэтому вместо сессии —
подписанный токен: адрес и действие, скреплённые HMAC на app_secret_key.
Подделать нельзя, в базе ничего хранить не нужно, срок жизни не ограничен —
ссылка на отписку в рассылке должна работать и через год.

Отписка обязательна в самих рассылках (иначе почтовые провайдеры считают
письмо спамом), подписка — в служебном письме с кодом, и только тем, кто ещё
не подписан.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.core import email_address
from app.models import User

logger = logging.getLogger(__name__)

ACTION_SUBSCRIBE = "sub"
ACTION_UNSUBSCRIBE = "unsub"


class NewsletterTokenError(Exception):
    """Ссылка испорчена или подписана не нашим ключом."""


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_token(normalized_email: str, action: str, secret: str) -> str:
    payload = f"{action}:{normalized_email}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_signature(payload, secret)}"


def parse_token(token: str, secret: str) -> tuple[str, str]:
    """Вернуть (действие, нормализованный адрес). Бросает NewsletterTokenError."""
    if "." not in token:
        raise NewsletterTokenError("Malformed token.")
    encoded, _, signature = token.rpartition(".")
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise NewsletterTokenError("Malformed token.") from exc

    if not hmac.compare_digest(signature, _signature(payload, secret)):
        raise NewsletterTokenError("Bad signature.")

    action, _, normalized = payload.partition(":")
    if action not in {ACTION_SUBSCRIBE, ACTION_UNSUBSCRIBE} or not normalized:
        raise NewsletterTokenError("Unknown action.")
    return action, normalized


def subscribe_url(settings: Settings, normalized_email: str) -> str:
    # Через /api: страницу отдаёт бэкенд, отдельного фронтового роута для неё
    # нет — человек приходит по ссылке из письма и уходит обратно в почту.
    token = make_token(normalized_email, ACTION_SUBSCRIBE, settings.app_secret_key)
    return f"{settings.app_base_url.rstrip('/')}/api/news/subscribe?token={token}"


def unsubscribe_url(settings: Settings, normalized_email: str) -> str:
    token = make_token(normalized_email, ACTION_UNSUBSCRIBE, settings.app_secret_key)
    return f"{settings.app_base_url.rstrip('/')}/api/news/unsubscribe?token={token}"


def find_user_by_mailbox(db: Session, normalized_email: str) -> User | None:
    # Локальный импорт: email_auth_service тянет мейлер, а сюда ходят и роуты.
    from app.services.email_auth_service import find_identity_by_mailbox

    identity = find_identity_by_mailbox(db, normalized_email)
    return identity.user if identity is not None else None


def is_subscribed(db: Session, raw_email: str) -> bool:
    user = find_user_by_mailbox(db, email_address.normalize(raw_email))
    return bool(user and user.news_subscribed)


def apply_token(db: Session, token: str, settings: Settings) -> tuple[bool, str]:
    """Применить ссылку из письма. Возвращает (подписан ли теперь, адрес)."""
    action, normalized = parse_token(token, settings.app_secret_key)
    user = find_user_by_mailbox(db, normalized)
    if user is None:
        # Профиль удалили или почту отвязали — отписка всё равно должна
        # выглядеть успешной: человек своё действие совершил.
        return action == ACTION_SUBSCRIBE, normalized

    user.news_subscribed = action == ACTION_SUBSCRIBE
    db.commit()
    logger.info("newsletter: %s for user %s", action, user.id)
    return user.news_subscribed, normalized
