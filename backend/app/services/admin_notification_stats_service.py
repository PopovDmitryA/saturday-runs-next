"""Статистика админки: кто подписан на уведомления и куда они уходят.

Подписчик — человек, у которого включён хотя бы один канал: главного
выключателя нет (см. UserNotificationPrefs). Срезы:

* каналы — сколько людей держит канал включённым, сколько из них проверку
  доставки не прошли, кто выбрал его основным, сколько ушло за период;
* наборы каналов — человек попадает ровно в одну строку;
* виды — у скольких подписчиков вид включён с учётом умолчаний реестра;
* системы для отмен стартов — пустой выбор значит «все системы»;
* журнал доставок за период по видам.

«Не доходит» — подписчик, у которого ВСЕ включённые каналы провалили проверку:
ему показывается окно «Мы не можем вам написать».
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NotificationDelivery, User, UserNotificationChannel, UserNotificationPrefs
from app.notification_kinds import NOTIFICATION_KINDS, kind_enabled
from app.services.notification_channels_service import CHANNEL_ORDER, CHANNEL_TITLES
from app.services.notification_service import STATUS_FAILED, STATUS_SENT


def _pct(part: int, total: int) -> float:
    return round(part * 100 / total, 1) if total else 0.0


def get_admin_notification_stats(db: Session, *, period_days: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(days=period_days)

    users_total = int(db.query(func.count(User.id)).scalar() or 0)

    channel_rows = db.query(
        UserNotificationChannel.user_id,
        UserNotificationChannel.channel,
        UserNotificationChannel.enabled,
        UserNotificationChannel.check_ok,
        UserNotificationChannel.created_at,
    ).all()
    enabled_by_user: dict[UUID, list[tuple[str, bool | None, datetime]]] = defaultdict(list)
    users_with_rows: set[UUID] = set()
    per_channel: dict[str, Counter[str]] = {code: Counter() for code in CHANNEL_ORDER}
    for user_id, channel, enabled, check_ok, created_at in channel_rows:
        users_with_rows.add(user_id)
        counter = per_channel.setdefault(channel, Counter())
        if not enabled:
            counter["disabled"] += 1
            continue
        enabled_by_user[user_id].append((channel, check_ok, created_at))
        counter["enabled"] += 1
        counter["check_ok" if check_ok is True else "check_failed" if check_ok is False else "unchecked"] += 1

    subscribers = set(enabled_by_user)
    unreachable = sum(1 for items in enabled_by_user.values() if all(check_ok is False for _c, check_ok, _t in items))
    # С какого момента человек подписан — самый ранний из включённых каналов.
    subscribed_at = {user_id: min(created for _c, _ok, created in items) for user_id, items in enabled_by_user.items()}
    new_in_period = [moment for moment in subscribed_at.values() if moment >= since]

    prefs_by_user = {prefs.user_id: prefs for prefs in db.query(UserNotificationPrefs).all()}
    primary = Counter(
        prefs.primary_channel
        for user_id, prefs in prefs_by_user.items()
        if user_id in subscribers and prefs.primary_channel
    )
    dismissed = sum(1 for prefs in prefs_by_user.values() if prefs.nudge_dismissed_at is not None)

    sent_rows = (
        db.query(NotificationDelivery.channel, NotificationDelivery.status, func.count(NotificationDelivery.id))
        .filter(NotificationDelivery.created_at >= since)
        .group_by(NotificationDelivery.channel, NotificationDelivery.status)
        .all()
    )
    sent_by_channel: Counter[str] = Counter()
    for channel, status, count in sent_rows:
        if status == STATUS_SENT and channel:
            sent_by_channel[channel] += int(count)

    channels = []
    for code in list(CHANNEL_ORDER) + [c for c in per_channel if c not in CHANNEL_ORDER]:
        counter = per_channel.get(code, Counter())
        channels.append(
            {
                "channel": code,
                "title": CHANNEL_TITLES.get(code, code),
                "enabled": counter["enabled"],
                "disabled": counter["disabled"],
                "check_ok": counter["check_ok"],
                "check_failed": counter["check_failed"],
                "unchecked": counter["unchecked"],
                "primary": primary.get(code, 0),
                "sent_period": sent_by_channel.get(code, 0),
            }
        )

    order = {code: index for index, code in enumerate(CHANNEL_ORDER)}
    combos = Counter(
        tuple(sorted({channel for channel, _ok, _t in items}, key=lambda c: order.get(c, 99)))
        for items in enabled_by_user.values()
    )
    combinations = [
        {"channels": list(combo), "users": count}
        for combo, count in sorted(combos.items(), key=lambda item: (-item[1], item[0]))
    ]

    kinds = []
    for kind in NOTIFICATION_KINDS:
        if not kind.available:
            continue
        on = sum(
            1
            for user_id in subscribers
            if kind_enabled(prefs_by_user[user_id].kinds if user_id in prefs_by_user else None, kind.code)
        )
        kinds.append(
            {
                "code": kind.code,
                "title": kind.title,
                "default_enabled": kind.default_enabled,
                "enabled": on,
                "share": _pct(on, len(subscribers)),
            }
        )

    # Системы для отмен — только у тех, кому отмены вообще приходят.
    cancellation_platforms: Counter[str] = Counter()
    for user_id in subscribers:
        prefs = prefs_by_user.get(user_id)
        if not kind_enabled(prefs.kinds if prefs else None, "cancellations"):
            continue
        chosen = (prefs.cancellation_platforms if prefs else None) or []
        if not chosen:
            cancellation_platforms["all"] += 1
        for code in chosen:
            cancellation_platforms[code] += 1

    kind_rows = (
        db.query(NotificationDelivery.kind, NotificationDelivery.status, func.count(NotificationDelivery.id))
        .filter(NotificationDelivery.created_at >= since)
        .group_by(NotificationDelivery.kind, NotificationDelivery.status)
        .all()
    )
    titles = {kind.code: kind.title for kind in NOTIFICATION_KINDS}
    deliveries: dict[str, Counter[str]] = defaultdict(Counter)
    for kind_code, status, count in kind_rows:
        deliveries[kind_code][status] += int(count)
    deliveries_by_kind = [
        {
            "code": code,
            "title": titles.get(code, code),
            "sent": counter[STATUS_SENT],
            "failed": counter[STATUS_FAILED],
            "other": sum(counter.values()) - counter[STATUS_SENT] - counter[STATUS_FAILED],
        }
        for code, counter in sorted(deliveries.items(), key=lambda item: -sum(item[1].values()))
    ]

    today = now.date()
    days = [today - timedelta(days=offset) for offset in range(period_days - 1, -1, -1)]
    per_day: Counter[date] = Counter(moment.date() for moment in new_in_period)
    new_by_day = [{"date": day, "value": per_day.get(day, 0)} for day in days]

    return {
        "period_days": period_days,
        "totals": {
            "users_total": users_total,
            "subscribers": len(subscribers),
            "subscribers_share": _pct(len(subscribers), users_total),
            "new_subscribers_period": len(new_in_period),
            # Каналы заведены, но все выключены — человек отказался сам.
            "opted_out": len(users_with_rows - subscribers),
            "unreachable": unreachable,
            "nudge_dismissed": dismissed,
            "sent_period": sum(sent_by_channel.values()),
        },
        "channels": channels,
        "combinations": combinations,
        "kinds": kinds,
        "cancellation_platforms": [
            {"code": code, "users": count} for code, count in cancellation_platforms.most_common()
        ],
        "deliveries_by_kind": deliveries_by_kind,
        "new_by_day": new_by_day,
    }
