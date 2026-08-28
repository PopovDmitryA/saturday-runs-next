"""Мониторинг отмен: одно сообщение на все изменения и тишина, когда их нет."""

from __future__ import annotations

from unittest.mock import patch

from app.services.location_cancellation_notify import (
    CancellationChange,
    format_cancellation_report,
    notify_cancellation_changes,
)


def test_report_lists_cancellations_and_restores() -> None:
    text = format_cancellation_report(
        [
            CancellationChange(
                platform_code="s95",
                slug="ivanovo",
                name="Иваново",
                cancelled=True,
                reason="Отмена забега 29 августа",
            ),
            CancellationChange(
                platform_code="five_verst",
                slug="buzuluk",
                name="Бузулук",
                cancelled=False,
            ),
        ],
        base_url="https://run5k.run",
    )

    assert "🚫 Отмена ближайшего старта" in text
    assert "S95 · Иваново" in text
    assert "Причина: Отмена забега 29 августа" in text
    assert "https://run5k.run/locations/ivanovo" in text
    assert "✅ Отмена снята" in text
    assert "5 вёрст · Бузулук" in text


def test_nothing_changed_means_nothing_sent() -> None:
    with patch("app.services.location_cancellation_notify.notify_admin") as notify:
        assert notify_cancellation_changes([]) is False
    assert notify.call_count == 0


def test_failed_delivery_does_not_break_the_sync() -> None:
    with patch(
        "app.services.location_cancellation_notify.notify_admin",
        side_effect=RuntimeError("прокси легла"),
    ):
        assert (
            notify_cancellation_changes(
                [CancellationChange(platform_code="s95", slug="ivanovo", name="Иваново", cancelled=True)]
            )
            is False
        )
