"""Админ-команды бота ходят в API асинхронно.

Синхронный httpx в корутине aiogram останавливал событийный цикл бота на всё
время запроса: не подтверждались входы других людей и не писался heartbeat с
TTL 90 с (BOT-01). Подменяем httpx.AsyncClient.request — так же, как это делает
живой код; корутину крутим через asyncio.run: pytest-asyncio в проекте нет.
"""

from __future__ import annotations

import asyncio
import inspect

import httpx
import pytest

from bot_app import admin_ops
from bot_app.settings import BotSettings

ADMIN_ID = 42


@pytest.fixture
def settings() -> BotSettings:
    return BotSettings(
        telegram_bot_token="test-token",
        telegram_bot_internal_secret="bot-secret",
        admin_telegram_id=ADMIN_ID,
        api_base_url="http://api-test:8000",
    )


def fake_request(response: httpx.Response, recorder: list[dict] | None = None):
    async def _request(self: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> httpx.Response:
        if recorder is not None:
            recorder.append({"method": method, "url": url, **kwargs})
        return response

    return _request


def test_every_admin_op_is_a_coroutine() -> None:
    """Сторож BOT-01: синхронная функция здесь снова заблокирует цикл бота."""
    for name in ("fetch_stats", "fetch_pipeline_status", "list_pipelines", "enqueue_pipeline", "sync_protocol_url"):
        assert inspect.iscoroutinefunction(getattr(admin_ops, name)), name


def test_fetch_stats_formats_payload(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    payload = {"period_days": 7, "overview": {"users_total": 3}}
    calls: list[dict] = []
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request(httpx.Response(200, json=payload), calls))

    text = asyncio.run(admin_ops.fetch_stats(settings, 7))

    assert calls[0]["url"] == "http://api-test:8000/api/internal/bot/admin/stats"
    assert calls[0]["params"] == {"telegram_id": ADMIN_ID, "period_days": 7}
    assert "Статистика ЛК за 7 дн." in text
    assert "Пользователи: 3" in text


def test_fetch_stats_raises_on_error(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "request", fake_request(httpx.Response(403, json={"detail": "Admin access required"}))
    )
    with pytest.raises(RuntimeError, match="Admin access required"):
        asyncio.run(admin_ops.fetch_stats(settings, 30))


def test_enqueue_pipeline_success(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        httpx.AsyncClient,
        "request",
        fake_request(httpx.Response(200, json={"message": "Поставлена в очередь: 5v location zil"}), calls),
    )

    result = asyncio.run(admin_ops.enqueue_pipeline(settings, "location", location_slug="zil"))

    assert calls[0]["url"] == "http://api-test:8000/api/internal/bot/admin/sync-enqueue"
    assert calls[0]["json"] == {"pipeline": "location", "location_slug": "zil"}
    assert result == "Поставлена в очередь: 5v location zil"


def test_enqueue_pipeline_unknown_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, settings: BotSettings
) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "request", fake_request(httpx.Response(400, json={"detail": "Неизвестный пайплайн"}))
    )
    with pytest.raises(ValueError, match="Неизвестный пайплайн"):
        asyncio.run(admin_ops.enqueue_pipeline(settings, "bogus"))


def test_sync_protocol_url_formats_result(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    payload = {
        "platform": "five_verst",
        "location_slug": "zil",
        "event_date": "2026-07-25",
        "run_results_upserted": 12,
        "volunteer_results_upserted": 2,
        "protocol_changed": True,
    }
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request(httpx.Response(200, json=payload)))

    text = asyncio.run(admin_ops.sync_protocol_url(settings, "https://5verst.ru/zil/results/25.07.2026/"))

    assert "Протокол обновлён (five_verst)" in text
    assert "Пробежек: 12, волонтёров: 2" in text
    assert "Протокол изменился: да" in text


def test_list_pipelines_parses_items(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient,
        "request",
        fake_request(httpx.Response(200, json=[{"key": "registry", "label": "5v registry"}])),
    )
    assert asyncio.run(admin_ops.list_pipelines(settings)) == [("registry", "5v registry")]
