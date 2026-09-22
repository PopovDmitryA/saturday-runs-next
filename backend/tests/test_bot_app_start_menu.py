from __future__ import annotations

import asyncio
import importlib
from collections.abc import Generator

import pytest

ADMIN_ID = 777


@pytest.fixture
def bot_main(monkeypatch: pytest.MonkeyPatch) -> Generator[object, None, None]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_BOT_INTERNAL_SECRET", "bot-secret")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", str(ADMIN_ID))
    monkeypatch.setenv("APP_BASE_URL", "https://run5k.run")
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)

    import bot_app.main as module

    importlib.reload(module)
    yield module


class FakeBot:
    """Запоминает, что бот отправил в set_my_commands, вместо похода в Telegram."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[object], object]] = []

    async def set_my_commands(self, commands, scope=None):  # noqa: ANN001, ANN201
        self.calls.append((commands, scope))


def test_start_message_is_about_the_site_not_about_coordinates(bot_main: object) -> None:
    """Инструкция про координаты s95 — админская: посторонним она ничего не объясняет."""

    text = bot_main.START_MESSAGE
    assert "Войти через Telegram" in text
    assert "latitude" not in text
    assert "Saturday Runs" not in text


def test_coordinates_hint_moved_to_admin_help(bot_main: object) -> None:
    assert "latitude:longitude" in bot_main.ADMIN_HELP_TEXT


def test_site_keyboard_only_for_public_https(bot_main: object) -> None:
    keyboard = bot_main._site_keyboard()
    assert keyboard is not None
    assert keyboard.inline_keyboard[0][0].url == "https://run5k.run"

    bot_main.settings.app_base_url = "http://localhost:8080"
    assert bot_main._site_keyboard() is None


def test_publish_commands_gives_admin_his_own_list(bot_main: object) -> None:
    bot = FakeBot()
    asyncio.run(bot_main._publish_commands(bot))

    assert len(bot.calls) == 2
    user_commands, _ = bot.calls[0]
    admin_commands, admin_scope = bot.calls[1]

    assert [item.command for item in user_commands] == ["start", "help"]
    assert "sync" in [item.command for item in admin_commands]
    assert admin_scope.chat_id == ADMIN_ID


def test_publish_commands_skips_admin_scope_when_id_unknown(bot_main: object) -> None:
    bot_main.settings.admin_telegram_id = 0
    bot_main.settings.telegram_admin_chat_id = 0
    bot = FakeBot()
    asyncio.run(bot_main._publish_commands(bot))

    assert len(bot.calls) == 1


def test_publish_commands_survives_telegram_outage(bot_main: object) -> None:
    """Без меню бот работает, без polling — нет: падение здесь не должно ронять старт."""

    class BrokenBot:
        async def set_my_commands(self, commands, scope=None):  # noqa: ANN001, ANN201
            raise RuntimeError("Telegram недоступен")

    asyncio.run(bot_main._publish_commands(BrokenBot()))
