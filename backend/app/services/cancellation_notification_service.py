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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Location, Platform, User, UserNotificationChannel, UserNotificationPrefs
from app.notification_kinds import kind_enabled
from app.notification_markup import bold, link
from app.saturday_week import week_saturday
from app.services import notification_service as notifications
from app.services.platform_titles import PLATFORM_ORDER, platform_title

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


# ---------------------------------------------------------------------------
# Пятничная сводка: что в итоге отменено на завтра
#
# Отмены приходят всю неделю по мере появления, и к субботе из них не собрать
# картину — какие-то сняли, какие-то добавились. Поэтому в пятницу в 21:00 по
# местному времени человека уходит итог: все площадки, где завтра старта не
# будет. Отмен нет — сообщение всё равно уходит, «отмен нет» тоже ответ
# (решение Дмитрия 25.09.2026). Местное время — по домашней локации: у неё
# есть часовой пояс; дома нет — считаем по Москве.

SUMMARY_DEDUPE_PREFIX = "cancel-friday"
# Окно отправки по местному времени: с 21:00 до 23:00. Задача ходит раз в час,
# и один пропущенный заход (перезапуск beat, деплой) не должен съесть сводку;
# повтор внутри окна отсекает dedupe по дате субботы.
SUMMARY_HOUR_FROM = 21
SUMMARY_HOUR_TO = 23
SUMMARY_MAX_LISTED = 30
FALLBACK_TIMEZONE = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class CancelledLocation:
    platform_code: str
    slug: str
    name: str
    reason: str | None


def cancelled_locations(db: Session) -> list[CancelledLocation]:
    """Площадки с отменой ближайшего старта прямо сейчас, в порядке систем
    портала и по алфавиту внутри системы."""
    rows = db.execute(
        select(Location.external_key, Location.name, Location.cancel_reason, Platform.code)
        .join(Platform, Platform.id == Location.platform_id)
        .where(Location.is_cancelled.is_(True))
    ).all()
    order = {code: index for index, code in enumerate(PLATFORM_ORDER)}
    items = [
        CancelledLocation(platform_code=code, slug=key, name=name, reason=(reason or "").strip() or None)
        for key, name, reason, code in rows
    ]
    return sorted(items, key=lambda item: (order.get(item.platform_code, len(order)), item.name.casefold()))


def _location_zones(db: Session) -> dict[str, tzinfo]:
    """Канонический ключ площадки -> часовой пояс. IANA-зона из сбора погоды
    точнее, смещение от Москвы — запасной вариант."""
    from app.services.leaderboard_service import _location_identity_maps

    identity_by_location, _names, _slugs = _location_identity_maps(db)
    zones: dict[str, tzinfo] = {}
    rows = db.execute(select(Location.id, Location.timezone, Location.tz_offset_moscow)).all()
    for location_id, zone_name, offset in rows:
        identity = identity_by_location.get(location_id)
        if identity is None:
            continue
        zone: tzinfo | None = None
        if zone_name:
            try:
                zone = ZoneInfo(zone_name)
            except (ZoneInfoNotFoundError, ValueError):
                zone = None
        if zone is None and offset is not None:
            zone = timezone(timedelta(hours=3 + offset))
        if zone is None:
            continue
        # IANA-зона побеждает фиксированное смещение любой другой системы.
        if identity not in zones or isinstance(zone, ZoneInfo):
            zones[identity] = zone
    return zones


def user_zones(db: Session, user_ids: list[UUID]) -> dict[UUID, tzinfo]:
    """Часовой пояс человека — по домашней локации (ручной выбор из настроек
    или автоматический, тем же правилом, что в кабинете)."""
    from app.services.admin_home_location_service import resolve_admin_home_locations

    if not user_ids:
        return {}
    zones = _location_zones(db)
    homes = {user_id: home.identity_key for user_id, home in resolve_admin_home_locations(db, user_ids).items()}
    # Дом выбран руками, но пробежек там ещё нет — выбор всё равно его.
    for user_id, key in db.execute(
        select(User.id, User.home_location_key).where(User.id.in_(user_ids), User.home_location_key.isnot(None))
    ).all():
        homes.setdefault(user_id, key)
    return {user_id: zones.get(key, FALLBACK_TIMEZONE) for user_id, key in homes.items()}


def summary_due(local_now: datetime) -> bool:
    return local_now.weekday() == 4 and SUMMARY_HOUR_FROM <= local_now.hour < SUMMARY_HOUR_TO


def _locations_word(count: int) -> str:
    """«на 1 локации», «на 21 локации», остальное — «на N локациях»."""
    return "локации" if count % 10 == 1 and count % 100 != 11 else "локациях"


def compose_summary(
    items: list[CancelledLocation], *, saturday: date, base_url: str, platforms: list[str] | None = None
) -> tuple[str, str]:
    """(заголовок, тело) пятничной сводки. platforms — выбранные в настройках
    системы (пусто — все): при фильтре так и пишем, по каким смотрели."""
    day = f"{saturday.day:02d}.{saturday.month:02d}"
    scope = ""
    if platforms:
        scope = "\n\nПо системам: " + ", ".join(platform_title(code) for code in platforms) + "."
    if not items:
        return f"✅ Отмен на завтра, {day}, нет", f"Ни одна локация не сообщила об отмене субботнего старта.{scope}"

    lines = []
    for item in items[:SUMMARY_MAX_LISTED]:
        line = f"📍 {link(bold(item.name), f'{base_url}/locations/{item.slug}')} · {platform_title(item.platform_code)}"
        if item.reason:
            line += f"\n{item.reason}"
        lines.append(line)
    rest = len(items) - SUMMARY_MAX_LISTED
    if rest > 0:
        lines.append(f"… и ещё {rest}")
    head = f"Завтра старта не будет на {len(items)} {_locations_word(len(items))}:"
    return f"🗓 Отмены стартов на завтра, {day}", head + "\n\n" + "\n\n".join(lines) + scope


def send_friday_summaries(db: Session, *, now: datetime | None = None) -> int:
    """Раздать сводку тем, у кого сейчас вечер пятницы. Возвращает число
    поставленных в очередь сообщений."""
    now = now or datetime.now(UTC)
    user_ids = subscriber_ids(db)
    if not user_ids:
        return 0
    zones = user_zones(db, user_ids)
    due: list[tuple[UUID, date]] = []
    for user_id in user_ids:
        local_now = now.astimezone(zones.get(user_id, FALLBACK_TIMEZONE))
        if summary_due(local_now):
            due.append((user_id, local_now.date() + timedelta(days=1)))
    if not due:
        return 0

    base_url = get_settings().app_base_url.rstrip("/")
    items = cancelled_locations(db)
    users = {user.id: user for user in db.execute(select(User).where(User.id.in_([u for u, _ in due]))).scalars()}
    queued: list[UUID] = []
    for user_id, saturday in due:
        user = users.get(user_id)
        if user is None:
            continue
        prefs = notifications.get_prefs(db, user_id)
        if not kind_enabled(prefs.kinds if prefs else None, KIND):
            continue
        mine = [item for item in items if platform_allowed(prefs, item.platform_code)]
        chosen = list(prefs.cancellation_platforms or []) if prefs is not None else []
        title, text = compose_summary(mine, saturday=saturday, base_url=base_url, platforms=chosen)
        delivery = notifications.notify_user(
            db,
            user,
            KIND,
            title=title,
            text=text,
            dedupe_key=f"{SUMMARY_DEDUPE_PREFIX}:{saturday.isoformat()}",
            url=None,
            commit=False,
        )
        if delivery is not None:
            queued.append(delivery.id)
    db.commit()
    for delivery_id in queued:
        notifications.enqueue_delivery(delivery_id)
    return len(queued)
