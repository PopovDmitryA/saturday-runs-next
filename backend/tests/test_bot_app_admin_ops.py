from __future__ import annotations

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


def test_fetch_stats_formats_payload(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    payload = {"period_days": 7, "overview": {"users_total": 3}}

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://api-test:8000/api/internal/bot/admin/stats"
        assert kwargs["params"] == {"telegram_id": ADMIN_ID, "period_days": 7}
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(admin_ops.httpx, "get", fake_get)
    text = admin_ops.fetch_stats(settings, 7)
    assert "Статистика ЛК за 7 дн." in text
    assert "Пользователи: 3" in text


def test_fetch_stats_raises_on_error(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    monkeypatch.setattr(
        admin_ops.httpx, "get", lambda *a, **k: httpx.Response(403, json={"detail": "Admin access required"})
    )
    with pytest.raises(RuntimeError, match="Admin access required"):
        admin_ops.fetch_stats(settings, 30)


def test_enqueue_pipeline_success(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        assert url == "http://api-test:8000/api/internal/bot/admin/sync-enqueue"
        assert kwargs["json"] == {"pipeline": "location", "location_slug": "zil"}
        return httpx.Response(200, json={"message": "Поставлена в очередь: 5v location zil"})

    monkeypatch.setattr(admin_ops.httpx, "post", fake_post)
    result = admin_ops.enqueue_pipeline(settings, "location", location_slug="zil")
    assert result == "Поставлена в очередь: 5v location zil"


def test_enqueue_pipeline_unknown_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, settings: BotSettings
) -> None:
    monkeypatch.setattr(
        admin_ops.httpx, "post", lambda *a, **k: httpx.Response(400, json={"detail": "Неизвестный пайплайн"})
    )
    with pytest.raises(ValueError, match="Неизвестный пайплайн"):
        admin_ops.enqueue_pipeline(settings, "bogus")


def test_sync_protocol_url_formats_result(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    payload = {
        "platform": "five_verst",
        "location_slug": "zil",
        "event_date": "2026-07-25",
        "run_results_upserted": 12,
        "volunteer_results_upserted": 2,
        "protocol_changed": True,
    }
    monkeypatch.setattr(admin_ops.httpx, "post", lambda *a, **k: httpx.Response(200, json=payload))
    text = admin_ops.sync_protocol_url(settings, "https://5verst.ru/zil/results/25.07.2026/")
    assert "Протокол обновлён (five_verst)" in text
    assert "Пробежек: 12, волонтёров: 2" in text
    assert "Протокол изменился: да" in text


def test_list_pipelines_parses_items(monkeypatch: pytest.MonkeyPatch, settings: BotSettings) -> None:
    monkeypatch.setattr(
        admin_ops.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=[{"key": "registry", "label": "5v registry"}]),
    )
    assert admin_ops.list_pipelines(settings) == [("registry", "5v registry")]
