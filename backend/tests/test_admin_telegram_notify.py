from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services import admin_telegram_notify as module


@pytest.fixture
def admin_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        telegram_bot_token="test-token",
        telegram_admin_chat_id=9001,
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(module, "get_settings", lambda: settings)


def test_send_admin_report_success_stays_quiet_in_vk(
    monkeypatch: pytest.MonkeyPatch, admin_settings: Settings
) -> None:
    _patch_settings(monkeypatch, admin_settings)
    monkeypatch.setattr(module.httpx, "post", lambda *a, **k: httpx.Response(200, json={"ok": True}))

    vk_calls: list[str] = []
    monkeypatch.setattr(module, "send_vk_admin_message", lambda text, **k: vk_calls.append(text))

    module.send_admin_report("отчёт 1")

    assert vk_calls == []
    assert module._is_degraded() is False


def test_send_admin_report_failure_alerts_and_duplicates_to_vk(
    monkeypatch: pytest.MonkeyPatch, admin_settings: Settings
) -> None:
    _patch_settings(monkeypatch, admin_settings)
    monkeypatch.setattr(module.httpx, "post", lambda *a, **k: httpx.Response(502, text="bad gateway"))

    vk_calls: list[str] = []
    monkeypatch.setattr(module, "send_vk_admin_message", lambda text, **k: vk_calls.append(text))

    module.send_admin_report("отчёт 1")
    assert len(vk_calls) == 2
    assert "легла" in vk_calls[0]
    assert vk_calls[1] == "отчёт 1"
    assert module._is_degraded() is True

    module.send_admin_report("отчёт 2")
    assert len(vk_calls) == 3
    assert vk_calls[2] == "отчёт 2"


def test_send_admin_report_recovery_sends_one_off_notice(
    monkeypatch: pytest.MonkeyPatch, admin_settings: Settings
) -> None:
    _patch_settings(monkeypatch, admin_settings)
    vk_calls: list[str] = []
    monkeypatch.setattr(module, "send_vk_admin_message", lambda text, **k: vk_calls.append(text))

    monkeypatch.setattr(module.httpx, "post", lambda *a, **k: httpx.Response(502, text="bad gateway"))
    module.send_admin_report("отчёт 1")
    assert module._is_degraded() is True

    monkeypatch.setattr(module.httpx, "post", lambda *a, **k: httpx.Response(200, json={"ok": True}))
    module.send_admin_report("отчёт 2")

    assert module._is_degraded() is False
    assert "восстановлена" in vk_calls[-1]
