"""Мониторинг отмен: одно сообщение на все изменения и тишина, когда их нет."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    # «С95» с кириллической «С» — каноническое написание из platform_titles.
    assert "С95 · Иваново" in text
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


def test_admin_message_goes_without_open_transaction(db_session: Session) -> None:
    """notify_admin — сеть (Telegram/ВК): транзакцию, которую открыло чтение
    рассылки подписчикам, отпускаем до неё — Postgres рвёт соединение через
    60 с простоя, и следующий SQL синка упал бы."""
    seen: list[bool] = []

    def _subscribers_read_only(db: Session, changes: list[CancellationChange]) -> int:
        db.execute(select(1))  # ранний выход рассылки: прочитали — и ничего не записали
        return 0

    with (
        patch(
            "app.services.cancellation_notification_service.notify_cancellation_subscribers",
            side_effect=_subscribers_read_only,
        ),
        patch(
            "app.services.location_cancellation_notify.notify_admin",
            side_effect=lambda _text: seen.append(db_session.in_transaction()) or True,
        ),
    ):
        assert notify_cancellation_changes(
            [CancellationChange(platform_code="s95", slug="ivanovo", name="Иваново", cancelled=True)],
            db_session,
        )
    assert seen == [False]
