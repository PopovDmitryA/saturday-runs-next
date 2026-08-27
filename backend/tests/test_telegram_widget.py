from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.auth.providers.telegram_widget import TelegramAuthError, bot_id, verify

BOT_TOKEN = "123456789:AAExampleTokenForTestsOnly"


def _sign(data: dict[str, str], token: str = BOT_TOKEN) -> dict[str, str]:
    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hashlib.sha256(token.encode()).digest()
    signed = dict(data)
    signed["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return signed


def _payload(**overrides: str) -> dict[str, str]:
    data = {
        "id": "42",
        "first_name": "Дмитрий",
        "username": "runner",
        "auth_date": str(int(time.time())),
    }
    data.update(overrides)
    return _sign(data)


def test_bot_id_is_the_part_before_colon() -> None:
    """Виджету нужен только номер бота — токен целиком наружу отдавать нельзя."""
    assert bot_id(BOT_TOKEN) == "123456789"


def test_valid_payload_passes() -> None:
    profile = verify(_payload(), bot_token=BOT_TOKEN, max_age_seconds=86400)
    assert profile.telegram_id == 42
    assert profile.username == "runner"
    assert profile.first_name == "Дмитрий"


def test_tampered_field_breaks_the_signature() -> None:
    """Подмена id — самая очевидная атака: войти под чужим телеграмом."""
    payload = _payload()
    payload["id"] = "43"
    with pytest.raises(TelegramAuthError):
        verify(payload, bot_token=BOT_TOKEN, max_age_seconds=86400)


def test_signature_from_another_bot_is_rejected() -> None:
    payload = _sign(
        {"id": "42", "first_name": "Кто-то", "auth_date": str(int(time.time()))},
        token="987654321:AnotherBotToken",
    )
    with pytest.raises(TelegramAuthError):
        verify(payload, bot_token=BOT_TOKEN, max_age_seconds=86400)


def test_missing_hash_is_rejected() -> None:
    payload = _payload()
    payload.pop("hash")
    with pytest.raises(TelegramAuthError):
        verify(payload, bot_token=BOT_TOKEN, max_age_seconds=86400)


def test_stale_authorization_is_rejected() -> None:
    """Иначе однажды перехваченный набор данных работал бы вечно."""
    payload = _payload(auth_date=str(int(time.time()) - 90_000))
    with pytest.raises(TelegramAuthError):
        verify(payload, bot_token=BOT_TOKEN, max_age_seconds=86400)


def test_malformed_auth_date_is_rejected() -> None:
    payload = _payload(auth_date="вчера")
    with pytest.raises(TelegramAuthError):
        verify(payload, bot_token=BOT_TOKEN, max_age_seconds=86400)


def test_without_bot_token_nothing_passes() -> None:
    with pytest.raises(TelegramAuthError):
        verify(_payload(), bot_token="", max_age_seconds=86400)


def test_extra_fields_from_telegram_are_included_in_the_check() -> None:
    """Telegram может добавить поля — подпись считается по всем, что пришли."""
    payload = _payload(photo_url="https://t.me/i/userpic/320/runner.jpg", last_name="Попов")
    profile = verify(payload, bot_token=BOT_TOKEN, max_age_seconds=86400)
    assert profile.photo_url and profile.last_name == "Попов"
