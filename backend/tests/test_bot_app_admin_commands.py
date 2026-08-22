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
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)

    import bot_app.main as module

    importlib.reload(module)
    yield module


def test_aiohttp_session_accepts_configured_proxy() -> None:
    """aiogram строит прокси-коннектор только через aiohttp-socks — без этого пакета
    бот падает на старте с RuntimeError и уходит в крэш-луп (прод, 26.07.2026)."""
    from aiogram.client.session.aiohttp import AiohttpSession

    assert AiohttpSession(proxy="http://tg-proxy:8118") is not None


def test_is_admin_matches_configured_id(bot_main: object) -> None:
    assert bot_main._is_admin(ADMIN_ID) is True
    assert bot_main._is_admin(1) is False


def test_is_admin_false_when_unconfigured(monkeypatch: pytest.MonkeyPatch, bot_main: object) -> None:
    bot_main.settings.admin_telegram_id = 0
    assert bot_main._is_admin(0) is False
    assert bot_main._is_admin(ADMIN_ID) is False


def test_send_admin_reply_uses_configured_proxy(
    monkeypatch: pytest.MonkeyPatch, bot_main: object
) -> None:
    bot_main.settings.telegram_proxy_url = "http://tg-proxy:8118"
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["proxy"] = kwargs.get("proxy")

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> FakeResponse:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResponse()

    monkeypatch.setattr(bot_main.httpx, "AsyncClient", FakeAsyncClient)
    asyncio.run(bot_main._send_admin_reply(12345, "тест"))

    assert captured["proxy"] == "http://tg-proxy:8118"
    assert captured["url"] == "https://api.telegram.org/bottest-token/sendMessage"
    assert captured["json"] == {"chat_id": 12345, "text": "тест", "disable_web_page_preview": True}
