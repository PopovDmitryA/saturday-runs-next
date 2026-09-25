"""Статистика подписчиков уведомлений в админке."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import User, UserNotificationChannel, UserNotificationPrefs
from app.services.admin_notification_stats_service import get_admin_notification_stats


def _user(db: Session, name: str) -> User:
    user = User(display_name=name)
    db.add(user)
    db.flush()
    return user


def _channel(db: Session, user: User, channel: str, *, enabled: bool = True, check_ok: bool | None = True) -> None:
    db.add(
        UserNotificationChannel(
            user_id=user.id,
            channel=channel,
            external_id=uuid4().hex,
            enabled=enabled,
            check_ok=check_ok,
        )
    )
    db.flush()


def _snapshot(db: Session) -> dict[str, Any]:
    payload = get_admin_notification_stats(db, period_days=7)
    return {
        "totals": payload["totals"],
        "channels": {row["channel"]: row for row in payload["channels"]},
        "combos": {tuple(row["channels"]): row["users"] for row in payload["combinations"]},
        "kinds": {row["code"]: row["enabled"] for row in payload["kinds"]},
        "platforms": {row["code"]: row["users"] for row in payload["cancellation_platforms"]},
        "new_today": payload["new_by_day"][-1]["value"],
    }


def test_subscribers_by_channel_combination_and_kind(db_session: Session) -> None:
    before = _snapshot(db_session)

    both = _user(db_session, "ТГ и почта")
    _channel(db_session, both, "telegram")
    _channel(db_session, both, "email")
    db_session.add(
        UserNotificationPrefs(
            user_id=both.id,
            primary_channel="email",
            kinds={"backlog_new_cards": True, "ratings": False},
            cancellation_platforms=["s95"],
        )
    )

    blocked = _user(db_session, "Бот заблокирован")
    _channel(db_session, blocked, "telegram", check_ok=False)

    opted_out = _user(db_session, "Выключил сам")
    _channel(db_session, opted_out, "vk", enabled=False)

    _user(db_session, "Не подписан")
    db_session.flush()

    after = _snapshot(db_session)

    def delta(path: str) -> int:
        section, key = path.split(".")
        return int(after[section].get(key, 0)) - int(before[section].get(key, 0))

    assert delta("totals.users_total") == 4
    assert delta("totals.subscribers") == 2
    assert delta("totals.new_subscribers_period") == 2
    assert delta("totals.unreachable") == 1
    assert delta("totals.opted_out") == 1
    assert after["new_today"] - before["new_today"] == 2

    telegram_before, telegram_after = before["channels"]["telegram"], after["channels"]["telegram"]
    assert telegram_after["enabled"] - telegram_before["enabled"] == 2
    assert telegram_after["check_failed"] - telegram_before["check_failed"] == 1
    assert after["channels"]["email"]["primary"] - before["channels"]["email"]["primary"] == 1
    assert after["channels"]["vk"]["disabled"] - before["channels"]["vk"]["disabled"] == 1

    assert after["combos"].get(("telegram", "email"), 0) - before["combos"].get(("telegram", "email"), 0) == 1
    assert after["combos"].get(("telegram",), 0) - before["combos"].get(("telegram",), 0) == 1

    # Умолчания реестра: «Мои пробежки» у обоих, рейтинги один выключил,
    # «Новые карточки» по умолчанию выключены — включил один.
    assert delta("kinds.runs") == 2
    assert delta("kinds.ratings") == 1
    assert delta("kinds.backlog_new_cards") == 1

    assert delta("platforms.all") == 1
    assert delta("platforms.s95") == 1
