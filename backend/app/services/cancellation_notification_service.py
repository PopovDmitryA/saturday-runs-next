"""Личные уведомления об отмене ближайшего старта.

Отмены рассылаются по всей стране, а не только по «своим» площадкам: человек
может собираться в другой город, и отмена там важна ему не меньше, чем дома
(решение Дмитрия 23.09.2026). Единственный фильтр — системы: в настройках
можно оставить только 5 вёрст, только S95 или обе (пустой список = все).

Одно сообщение на весь набор изменений синка, а не письмо на каждую площадку:
в зимнюю субботу отмен бывает сразу несколько, и десяток сообщений подряд
читается как спам. Список у каждого свой — он собирается по выбранным
системам, поэтому текст компонуется на каждого получателя отдельно.

Отправку зовёт `location_cancellation_notify.notify_cancellation_changes`
сразу за админским сообщением — из всех трёх источников отмен (реестры
5 вёрст и S95, наблюдатель S95).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserNotificationChannel, UserNotificationPrefs
from app.notification_kinds import kind_enabled
from app.notification_markup import bold, link
from app.saturday_week import week_saturday
from app.services import notification_service as notifications
from app.services.platform_titles import platform_title

if TYPE_CHECKING:
    from app.services.location_cancellation_notify import CancellationChange

logger = logging.getLogger(__name__)

KIND = "cancellations"


def platform_allowed(prefs: UserNotificationPrefs | None, platform_code: str) -> bool:
    """Пустой список систем в настройках означает «все»."""
    if prefs is None:
        return True
    chosen = prefs.cancellation_platforms or []
    return not chosen or platform_code in chosen


def upcoming_saturday(today: date) -> date:
    """Ближайший субботний старт: в воскресенье это уже следующая суббота."""
    saturday = week_saturday(today)
    return saturday if saturday >= today else saturday + timedelta(days=7)


def _location_line(change: CancellationChange, *, marker: str, base_url: str) -> str:
    """Строка площадки: эмодзи-маркер отделяет одну локацию от другой, а само
    название — ссылка на её страницу у нас. Общей ссылки внизу поэтому нет:
    из списка на пять площадок нужна конкретная, а не каталог."""
    title = change.name or change.slug
    url = f"{base_url}/locations/{change.slug}"
    line = f"{marker} {link(bold(title), url)} · {platform_title(change.platform_code)}"
    if change.cancelled and change.reason:
        line += f"\n{change.reason}"
    return line


def compose(changes: list[CancellationChange], *, base_url: str, today: date | None = None) -> tuple[str, str]:
    """(заголовок, тело в разметке) на весь набор изменений одного человека.

    Состояние живёт в заголовке, пока весь набор одного вида: повторять его
    ещё и строкой над списком — значит сказать одно и то же дважды. В
    смешанном наборе состояние переезжает к каждой площадке своим эмодзи.
    """
    saturday = upcoming_saturday(today or date.today())
    day = f"{saturday.day:02d}.{saturday.month:02d}"
    cancelled = [item for item in changes if item.cancelled]
    restored = [item for item in changes if not item.cancelled]

    if cancelled and restored:
        title = f"🚫 Изменения по отменам стартов {day}"
        blocks = [_location_line(item, marker="🚫", base_url=base_url) for item in cancelled]
        blocks += [_location_line(item, marker="✅", base_url=base_url) for item in restored]
    elif cancelled:
        title = f"🚫 Отмена старта {day}" if len(cancelled) == 1 else f"🚫 Отмены стартов {day}"
        blocks = [_location_line(item, marker="📍", base_url=base_url) for item in cancelled]
    else:
        title = "✅ Отмена снята" if len(restored) == 1 else "✅ Отмены сняты"
        blocks = [_location_line(item, marker="📍", base_url=base_url) for item in restored]

    return title, "\n\n".join(blocks)


def _dedupe_key(changes: list[CancellationChange], *, today: date) -> str:
    """Один и тот же набор за одну неделю уходит один раз: синк реестра и
    наблюдатель S95 видят одну отмену по очереди."""
    week = today.isocalendar()
    parts = sorted(f"{c.platform_code}:{c.slug}:{int(c.cancelled)}" for c in changes)
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return f"cancel:{week[0]}-{week[1]:02d}:{digest}"


def subscriber_ids(db: Session) -> list[UUID]:
    """Все, у кого включён хотя бы один канал уведомлений."""
    return list(
        db.execute(select(UserNotificationChannel.user_id).where(UserNotificationChannel.enabled.is_(True)).distinct())
        .scalars()
        .all()
    )


def notify_cancellation_subscribers(
    db: Session, changes: list[CancellationChange], *, today: date | None = None
) -> int:
    """Разослать изменения отмен подписчикам. Возвращает число сообщений.

    Ошибка рассылки не имеет права ронять синк: данные уже записаны, а про
    неудачу расскажет лог.
    """
    if not changes:
        return 0
    today = today or date.today()
    base_url = get_settings().app_base_url.rstrip("/")
    queued: list[UUID] = []
    try:
        user_ids = subscriber_ids(db)
        if not user_ids:
            return 0
        users = db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
        for user in users:
            prefs = notifications.get_prefs(db, user.id)
            if not kind_enabled(prefs.kinds if prefs else None, KIND):
                continue
            mine = [item for item in changes if platform_allowed(prefs, item.platform_code)]
            if not mine:
                continue
            title, text = compose(mine, base_url=base_url, today=today)
            delivery = notifications.notify_user(
                db,
                user,
                KIND,
                title=title,
                text=text,
                dedupe_key=_dedupe_key(mine, today=today),
                # Ссылки-кнопки нет: каждая площадка в списке кликабельна сама.
                url=None,
                commit=False,
            )
            if delivery is not None:
                queued.append(delivery.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("cancellation notify: рассылка подписчикам не удалась")
        return 0
    for delivery_id in queued:
        notifications.enqueue_delivery(delivery_id)
    return len(queued)
