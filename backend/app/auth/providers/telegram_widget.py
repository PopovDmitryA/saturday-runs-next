"""Вход через Telegram Login Widget.

Отличие от остальных провайдеров принципиальное: наш сервер **не ходит** в
Telegram. Виджет в браузере человека получает данные от самого Telegram и
отдаёт их нам подписанными; мы лишь сверяем подпись ключом, выведенным из
токена бота. Ни одного исходящего запроса — а значит, ни прокси, ни его
доступности эта схема не требует. Для сервера в РФ, где api.telegram.org
недоступен напрямую, это решающее свойство.

Алгоритм проверки — из документации Telegram: строка из отсортированных пар
«ключ=значение» (без самого hash), HMAC-SHA256 на ключе SHA256(bot_token).
Сравнение обязательно постоянного времени: иначе подпись подбирается по
времени ответа.

auth_date проверяем отдельно: без него однажды перехваченный набор данных
работал бы вечно.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


class TelegramAuthError(Exception):
    """Данные виджета не прошли проверку."""


@dataclass(frozen=True)
class TelegramWidgetProfile:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    photo_url: str | None


def bot_id(bot_token: str) -> str:
    """Числовая часть токена — её ждёт виджет в браузере.

    Сам токен наружу отдавать нельзя: он равносилен полному доступу к боту.
    """
    return bot_token.split(":", 1)[0]


def verify(
    data: dict[str, str],
    *,
    bot_token: str,
    max_age_seconds: int,
) -> TelegramWidgetProfile:
    """Проверить подпись и свежесть, вернуть профиль."""
    if not bot_token:
        raise TelegramAuthError("Telegram login is not configured.")

    received_hash = data.get("hash")
    if not received_hash:
        raise TelegramAuthError("Telegram payload has no hash.")

    check_string = "\n".join(
        f"{key}={data[key]}" for key in sorted(data) if key != "hash" and data[key] is not None
    )
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise TelegramAuthError("Telegram signature mismatch.")

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError as exc:
        raise TelegramAuthError("Telegram auth_date is malformed.") from exc
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        raise TelegramAuthError("Telegram authorization is too old.")

    try:
        telegram_id = int(data["id"])
    except (KeyError, ValueError) as exc:
        raise TelegramAuthError("Telegram payload has no id.") from exc

    return TelegramWidgetProfile(
        telegram_id=telegram_id,
        username=data.get("username") or None,
        first_name=data.get("first_name") or None,
        last_name=data.get("last_name") or None,
        photo_url=data.get("photo_url") or None,
    )
