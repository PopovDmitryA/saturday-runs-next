from __future__ import annotations

import importlib
from collections.abc import Generator

import pytest


@pytest.fixture
def bot_main(monkeypatch: pytest.MonkeyPatch) -> Generator[object, None, None]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_BOT_INTERNAL_SECRET", "bot-secret")
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)

    import bot_app.main as module

    importlib.reload(module)
    yield module


def test_login_context_message_lists_device_city_time(bot_main: object) -> None:
    text = bot_main.format_login_context(
        {"browser": "Chrome", "os": "Android", "city": "Москва", "requested_at_label": "сегодня в 14:32 (МСК)"}
    )
    assert text.startswith("Вход на run5k.run")
    assert "🖥 Chrome, Android" in text
    assert "📍 Москва" in text
    assert "🕒 сегодня в 14:32 (МСК)" in text
    assert "Это не я" in text


def test_login_context_message_without_city_and_device(bot_main: object) -> None:
    text = bot_main.format_login_context(
        {"browser": "", "os": "", "city": "", "requested_at_label": ""}, link_mode=True
    )
    assert text.startswith("Привязка Telegram")
    assert "устройство не определено" in text
    assert "📍" not in text
    assert "🕒" not in text


def test_login_keyboard_callbacks(bot_main: object) -> None:
    plain = bot_main._login_keyboard("tok", with_consent=False)
    buttons = [button for row in plain.inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == ["login_confirm:tok", "login_decline:tok"]
    assert buttons[0].text == "✅ Подтвердить вход"

    consent = bot_main._login_keyboard("tok", with_consent=True)
    first = consent.inline_keyboard[0][0]
    assert first.callback_data == "login_consent:tok"
    assert "Принимаю" in first.text
