"""Админка «Уведомления»: сводка по журналу доставок и лента последних."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NotificationDelivery, User, UserNotificationChannel


def _counts(db: Session, column: Any, since: datetime) -> list[dict[str, Any]]:
    rows = (
        db.query(column, func.count(NotificationDelivery.id))
        .filter(NotificationDelivery.created_at >= since)
        .group_by(column)
        .order_by(func.count(NotificationDelivery.id).desc())
        .all()
    )
    return [{"key": str(key) if key is not None else "—", "count": int(count)} for key, count in rows]


def _user_label(user: User | None) -> str:
    if user is None:
        return "—"
    if user.telegram_username:
        return f"@{user.telegram_username}"
    return (user.display_name or "").strip() or f"№{user.serial_id}"


def get_admin_notifications(
    db: Session,
    *,
    period_days: int,
    status: str | None,
    kind: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(days=period_days)

    subscribers = (
        db.query(UserNotificationChannel.channel, func.count(UserNotificationChannel.id))
        .filter(UserNotificationChannel.enabled.is_(True))
        .group_by(UserNotificationChannel.channel)
        .all()
    )

    query = (
        db.query(NotificationDelivery, User)
        .outerjoin(User, User.id == NotificationDelivery.user_id)
        .filter(NotificationDelivery.created_at >= since)
    )
    if status:
        query = query.filter(NotificationDelivery.status == status)
    if kind:
        query = query.filter(NotificationDelivery.kind == kind)
    items_total = query.count()
    rows = query.order_by(NotificationDelivery.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "period_days": period_days,
        "generated_at": now,
        "total": db.query(func.count(NotificationDelivery.id)).filter(NotificationDelivery.created_at >= since).scalar()
        or 0,
        "by_status": _counts(db, NotificationDelivery.status, since),
        "by_kind": _counts(db, NotificationDelivery.kind, since),
        "by_channel": _counts(db, NotificationDelivery.channel, since),
        "subscribers_by_channel": [{"key": channel, "count": int(count)} for channel, count in subscribers],
        "items": [
            {
                "id": delivery.id,
                "user_serial_id": user.serial_id if user is not None else None,
                "user_label": _user_label(user),
                "kind": delivery.kind,
                "status": delivery.status,
                "channel": delivery.channel,
                "attempts": delivery.attempts,
                "title": str((delivery.payload or {}).get("title") or ""),
                "error": delivery.error,
                "created_at": delivery.created_at,
                "sent_at": delivery.sent_at,
            }
            for delivery, user in rows
        ],
        "items_total": items_total,
        "limit": limit,
        "offset": offset,
    }
