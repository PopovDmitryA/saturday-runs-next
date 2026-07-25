from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.login_journal_service import (
    EVENT_LOGIN,
    EVENT_LOGOUT,
    summarize_login_events,
)

BASE = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
PHONE = "device-phone"
LAPTOP = "device-laptop"


class FakeEvent:
    """Минимальный дубль LoginEvent: сводка читает только эти поля."""

    def __init__(self, *, minutes: int, event_type: str, device_ref: str, provider: str = "yandex") -> None:
        self.ts = BASE + timedelta(minutes=minutes)
        self.event_type = event_type
        self.device_ref = device_ref
        self.provider = provider
        self.ip = "203.0.113.1"
        self.user_agent = "UA"


def test_login_then_logout_then_login_is_expected() -> None:
    """Вышел сам и зашёл снова — это норма, а не потеря сессии."""
    events = [
        FakeEvent(minutes=0, event_type=EVENT_LOGIN, device_ref=PHONE),
        FakeEvent(minutes=10, event_type=EVENT_LOGOUT, device_ref=PHONE),
        FakeEvent(minutes=20, event_type=EVENT_LOGIN, device_ref=PHONE),
    ]

    summary = summarize_login_events(events)

    assert summary["unexpected_relogins"] == 0
    assert summary["logins"] == 2
    assert summary["logouts"] == 1


def test_repeated_login_without_logout_is_unexpected() -> None:
    """Повторный вход с того же устройства без разлогина — сессия слетела."""
    events = [
        FakeEvent(minutes=0, event_type=EVENT_LOGIN, device_ref=PHONE),
        FakeEvent(minutes=60, event_type=EVENT_LOGIN, device_ref=PHONE),
    ]

    summary = summarize_login_events(events)

    assert summary["unexpected_relogins"] == 1
    assert len(summary["unexpected_samples"]) == 1


def test_second_device_login_is_not_unexpected() -> None:
    """Вход с другого устройства не означает, что первая сессия умерла."""
    events = [
        FakeEvent(minutes=0, event_type=EVENT_LOGIN, device_ref=PHONE),
        FakeEvent(minutes=5, event_type=EVENT_LOGIN, device_ref=LAPTOP),
    ]

    summary = summarize_login_events(events)

    assert summary["unexpected_relogins"] == 0
    assert summary["devices"] == 2


def test_summary_is_order_independent() -> None:
    """Журнал приходит от новых к старым — сводка обязана сортировать сама."""
    newest_first = [
        FakeEvent(minutes=60, event_type=EVENT_LOGIN, device_ref=PHONE),
        FakeEvent(minutes=10, event_type=EVENT_LOGOUT, device_ref=PHONE),
        FakeEvent(minutes=0, event_type=EVENT_LOGIN, device_ref=PHONE),
    ]

    assert summarize_login_events(newest_first)["unexpected_relogins"] == 0
