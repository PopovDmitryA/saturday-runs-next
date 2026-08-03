"""Admin-уведомления от фич: канал Telegram и тишина на прогоне тестов."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core import runtime_env
from app.services import admin_notify, vk_admin_notify


def test_notify_admin_is_silent_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(admin_notify, "send_admin_report", sent.append)

    admin_notify.notify_admin("💬 Комментарий к «Идея» от Тест: Первый")

    assert sent == []


def test_notify_admin_goes_to_telegram_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(admin_notify, "send_admin_report", sent.append)
    monkeypatch.setattr(admin_notify, "is_test_run", lambda: False)

    admin_notify.notify_admin("🆕 Новая карточка бэклога")

    assert sent == ["🆕 Новая карточка бэклога"]


def test_notify_admin_dialog_returns_chat_and_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_notify, "is_test_run", lambda: False)
    monkeypatch.setattr(
        admin_notify,
        "get_settings",
        lambda: Settings(
            app_secret_key="test-secret-key", telegram_bot_token="t", telegram_admin_chat_id=4242
        ),
    )
    monkeypatch.setattr(admin_notify, "send_admin_telegram_message", lambda text, **k: 777)

    assert admin_notify.notify_admin_dialog("Локация без координат") == (4242, 777)


def test_notify_admin_dialog_is_silent_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_notify, "send_admin_telegram_message", lambda text, **k: 777)

    assert admin_notify.notify_admin_dialog("Локация без координат") is None


def test_vk_admin_message_skipped_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Даже с боевым токеном в .env прогон тестов не пишет админу в ВК."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        vk_admin_notify,
        "get_settings",
        lambda: Settings(app_secret_key="test-secret-key", vk_bot_group_token="t", vk_admin_user_id=1),
    )
    monkeypatch.setattr(vk_admin_notify, "send_vk_message", lambda *a, **k: calls.append(a) or 1)

    assert vk_admin_notify.send_vk_admin_message("тест") is None
    assert calls == []


def test_is_test_run_detects_pytest_and_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runtime_env.is_test_run() is True

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        runtime_env, "get_settings", lambda: Settings(app_secret_key="k", app_env="production")
    )
    assert runtime_env.is_test_run() is False

    monkeypatch.setattr(
        runtime_env, "get_settings", lambda: Settings(app_secret_key="k", app_env="test")
    )
    assert runtime_env.is_test_run() is True
