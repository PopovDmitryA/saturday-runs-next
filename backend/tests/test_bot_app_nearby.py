"""Бот: геопозиция → локации рядом, кнопки «на карте», inline-режим."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any

import pytest

from bot_app import nearby

ITEM: dict[str, Any] = {
    "identity_key": "catalog:5b2f3c1e-8d7a-4e1b-9f3c-2a1d4e5f6a7b",
    "name": "Сокольники",
    "latitude": 55.791959,
    "longitude": 37.664957,
    "distance_km": 3.0,
    "site_url": "https://run5k.run/locations/sokolniki",
    "status": "ok",
}


@pytest.fixture
def bot_main(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_BOT_INTERNAL_SECRET", "bot-secret")
    monkeypatch.setenv("APP_BASE_URL", "https://run5k.run")
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)

    import bot_app.main as module

    importlib.reload(module)
    yield module


def test_keyboard_has_map_and_site_button_per_location() -> None:
    keyboard = nearby.nearby_keyboard([ITEM, {**ITEM, "name": "Парк Горького", "site_url": None}])
    assert keyboard is not None
    first, second = keyboard.inline_keyboard
    assert first[0].text == "📍 Сокольники"
    assert first[0].callback_data == f"nearpin:{ITEM['identity_key']}"
    assert len(first[0].callback_data.encode()) <= 64
    assert first[1].url == "https://run5k.run/locations/sokolniki"
    # Без адреса страницы (или с локальным стендом) — только кнопка карты.
    assert len(second) == 1


def test_keyboard_skips_localhost_and_cuts_long_names() -> None:
    long_name = {**ITEM, "name": "Парк культуры и отдыха имени Горького", "site_url": "http://localhost:8080/x"}
    keyboard = nearby.nearby_keyboard([long_name])
    assert keyboard is not None
    (row,) = keyboard.inline_keyboard
    assert len(row) == 1
    assert row[0].text.endswith("…")
    assert len(row[0].text) <= nearby.PIN_BUTTON_NAME_MAX + 3


def test_inline_description_and_results() -> None:
    item = {
        **ITEM,
        "platform_title": "5 вёрст",
        "start_label": "🗓 завтра, 9:00 · старт ≈№137",
        "text_html": "<b>Сокольники</b>",
    }
    assert nearby.inline_description(item) == "3 км · 5 вёрст · завтра, 9:00 · старт ≈№137"
    (result,) = nearby.inline_results([item])
    assert result.title == "Сокольники"
    assert result.input_message_content.parse_mode == "HTML"
    buttons = result.reply_markup.inline_keyboard[0]
    assert buttons[0].url == "https://yandex.ru/maps/?pt=37.664957,55.791959&z=16&l=map"
    assert buttons[1].url == ITEM["site_url"]


class FakeMessage:
    def __init__(self) -> None:
        self.location = SimpleNamespace(latitude=55.75, longitude=37.61)
        self.chat = SimpleNamespace(id=42, type="private")
        self.from_user = SimpleNamespace(id=777)
        self.bot = SimpleNamespace(send_chat_action=self._chat_action)
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def _chat_action(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


def test_on_location_sends_api_text_with_keyboard(bot_main: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(settings: Any, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, payload))
        return {"has_nearby": True, "text_html": "📍 Локации рядом", "items": [ITEM]}

    monkeypatch.setattr(nearby, "_api_post", fake_post)
    message = FakeMessage()
    asyncio.run(nearby.on_location(message, bot_main.settings))  # type: ignore[arg-type]

    assert calls == [("/nearby", {"latitude": 55.75, "longitude": 37.61, "telegram_id": 777})]
    (text, kwargs) = message.answers[0]
    assert text == "📍 Локации рядом"
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("nearpin:")


def test_on_location_reports_api_outage(bot_main: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(settings: Any, path: str, payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(nearby, "_api_post", fake_post)
    message = FakeMessage()
    asyncio.run(nearby.on_location(message, bot_main.settings))  # type: ignore[arg-type]
    assert message.answers[0][0] == nearby.API_UNAVAILABLE_MESSAGE


def _message_with_me(me: Any) -> Any:
    async def get_me() -> Any:
        return me

    return SimpleNamespace(bot=SimpleNamespace(me=get_me))


def test_start_message_mentions_inline_only_when_enabled(bot_main: Any) -> None:
    enabled = SimpleNamespace(supports_inline_queries=True, username="weekend_runs_bot")
    text = asyncio.run(bot_main.start_message(_message_with_me(enabled)))
    assert "📍 Старты рядом" in text
    assert "@weekend_runs_bot" in text

    disabled = SimpleNamespace(supports_inline_queries=False, username="weekend_runs_bot")
    assert asyncio.run(bot_main.start_message(_message_with_me(disabled))) == bot_main.START_MESSAGE
    # Telegram не ответил — приветствие всё равно уходит.
    assert asyncio.run(bot_main.start_message(SimpleNamespace(bot=None))) == bot_main.START_MESSAGE
