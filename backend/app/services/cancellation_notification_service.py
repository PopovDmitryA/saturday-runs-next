"""Личные уведомления об отмене ближайшего старта.

Отмена касается одной субботы и одной площадки, поэтому рассылается не всем
подряд: сообщение получают те, для кого эта локация своя — домашняя либо та,
где человек бегал или волонтёрил за последний год. Плюс фильтр по системам:
в настройках можно оставить только 5 вёрст, только S95 и так далее (пустой
список = все системы).

Площадка считается одной точкой сквозь платформы: у локации берётся
канонический ключ каталога (см. location_catalog_service), и визиты
собираются по всем строкам `locations` с этим ключом. Иначе переезд площадки
5 вёрст → RunPark выглядел бы двумя разными местами, и половина её бегунов
уведомления бы не получила.

Отправку зовёт `location_cancellation_notify.notify_cancellation_changes`
сразу за админским сообщением — из всех трёх источников отмен (реестры
5 вёрст и S95, наблюдатель S95).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    UserNotificationChannel,
    UserNotificationPrefs,
    VolunteerResult,
)
from app.notification_kinds import kind_enabled
from app.notification_markup import bold, link
from app.saturday_week import week_saturday
from app.services import notification_service as notifications
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.platform_titles import platform_title

if TYPE_CHECKING:
    from app.services.location_cancellation_notify import CancellationChange

logger = logging.getLogger(__name__)

KIND = "cancellations"
# Насколько давно человек должен был появиться на площадке, чтобы отмена его
# касалась. Год — это полный цикл сезонов: бегущий там «иногда» в список
# попадает, случайный турист двухлетней давности — нет.
VISIT_WINDOW_DAYS = 365


@dataclass(frozen=True)
class CancellationRecipients:
    """Кому уходит одно изменение отмены и по какой локации."""

    identity_key: str
    location_ids: list[UUID]
    user_ids: set[UUID]


def platform_allowed(prefs: UserNotificationPrefs | None, platform_code: str) -> bool:
    """Пустой список систем в настройках означает «все»."""
    if prefs is None:
        return True
    chosen = prefs.cancellation_platforms or []
    return not chosen or platform_code in chosen


def _location_row(db: Session, platform_code: str, slug: str) -> Location | None:
    return (
        db.execute(
            select(Location)
            .join(Platform, Location.platform_id == Platform.id)
            .where(Platform.code == platform_code, Location.external_key == slug)
        )
        .scalars()
        .first()
    )


def _sibling_location_ids(db: Session, location: Location, platform_code: str) -> tuple[str, list[UUID]]:
    """Канонический ключ площадки и все строки `locations` под этим ключом."""
    index = LocationCatalogIndex(db)
    identity_key = index.canonical_identity_key(location, platform_code)
    if not identity_key.startswith("catalog:"):
        return identity_key, [location.id]
    rows = db.execute(select(Location, Platform.code).join(Platform, Location.platform_id == Platform.id)).all()
    ids = [row.id for row, code in rows if index.canonical_identity_key(row, code) == identity_key]
    return identity_key, ids or [location.id]


def _visitor_ids(db: Session, location_ids: list[UUID], *, since: date) -> set[UUID]:
    """Кто бегал или волонтёрил на площадке за окно визитов."""
    link_join = and_(
        PlatformLink.platform_id == Participant.platform_id,
        PlatformLink.external_user_id == Participant.external_user_id,
    )
    runners = (
        db.execute(
            select(PlatformLink.user_id)
            .select_from(RunResult)
            .join(Event, RunResult.event_id == Event.id)
            .join(Participant, RunResult.participant_id == Participant.id)
            .join(PlatformLink, link_join)
            .where(Event.location_id.in_(location_ids), Event.event_date >= since)
            .distinct()
        )
        .scalars()
        .all()
    )
    volunteers = (
        db.execute(
            select(PlatformLink.user_id)
            .select_from(VolunteerResult)
            .join(Event, VolunteerResult.event_id == Event.id)
            .join(Participant, VolunteerResult.participant_id == Participant.id)
            .join(PlatformLink, link_join)
            .where(Event.location_id.in_(location_ids), Event.event_date >= since)
            .distinct()
        )
        .scalars()
        .all()
    )
    return set(runners) | set(volunteers)


def find_recipients(
    db: Session, change: CancellationChange, *, today: date | None = None
) -> CancellationRecipients | None:
    """Кому интересна эта отмена: домашняя локация или визит за последний год.

    None — площадки нет в базе (такого быть не должно: изменение рождается
    из её же строки, но синк мог не успеть закоммитить).
    """
    location = _location_row(db, change.platform_code, change.slug)
    if location is None:
        logger.warning("cancellation notify: локация %s/%s не найдена", change.platform_code, change.slug)
        return None
    identity_key, location_ids = _sibling_location_ids(db, location, change.platform_code)
    since = (today or date.today()) - timedelta(days=VISIT_WINDOW_DAYS)

    # Сразу сужаем до тех, у кого уведомления включены: иначе в выборку
    # попадёт вся история площадки, а пишем мы единицам.
    enabled_users = set(
        db.execute(select(UserNotificationChannel.user_id).where(UserNotificationChannel.enabled.is_(True)).distinct())
        .scalars()
        .all()
    )
    if not enabled_users:
        return CancellationRecipients(identity_key=identity_key, location_ids=location_ids, user_ids=set())

    home_users = set(
        db.execute(select(User.id).where(User.id.in_(enabled_users), User.home_location_key == identity_key))
        .scalars()
        .all()
    )
    visitors = _visitor_ids(db, location_ids, since=since) & enabled_users
    return CancellationRecipients(identity_key=identity_key, location_ids=location_ids, user_ids=home_users | visitors)


def upcoming_saturday(today: date) -> date:
    """Ближайший субботний старт: в воскресенье это уже следующая суббота."""
    saturday = week_saturday(today)
    return saturday if saturday >= today else saturday + timedelta(days=7)


def compose(change: CancellationChange, *, base_url: str, today: date | None = None) -> tuple[str, str]:
    """(заголовок, тело в разметке) одного изменения."""
    title_name = change.name or change.slug
    system = platform_title(change.platform_code)
    url = f"{base_url}/locations/{change.slug}"
    saturday = upcoming_saturday(today or date.today())
    day = f"{saturday.day:02d}.{saturday.month:02d}"
    if change.cancelled:
        title = f"🚫 Отмена старта: {title_name}"
        lines = [f"{bold(title_name)} ({system}) — ближайший старт {day} отменён."]
        if change.reason:
            lines.append(f"Причина: {change.reason}")
        lines.append("Загляните на страницу локации перед выходом из дома.")
    else:
        title = f"✅ Отмена снята: {title_name}"
        lines = [f"{bold(title_name)} ({system}) — старт {day} снова состоится."]
    lines.append(link("Страница локации", url))
    return title, "\n\n".join(lines)


def notify_cancellation_subscribers(
    db: Session, changes: list[CancellationChange], *, today: date | None = None
) -> int:
    """Разослать изменения отмен тем, кого они касаются. Возвращает число писем.

    Ошибка рассылки не имеет права ронять синк: данные уже записаны, а про
    неудачу расскажет лог.
    """
    if not changes:
        return 0
    base_url = get_settings().app_base_url.rstrip("/")
    week = (today or date.today()).isocalendar()
    week_key = f"{week[0]}-{week[1]:02d}"
    queued: list[UUID] = []
    try:
        for change in changes:
            recipients = find_recipients(db, change, today=today)
            if recipients is None or not recipients.user_ids:
                continue
            title, text = compose(change, base_url=base_url, today=today)
            users = db.execute(select(User).where(User.id.in_(recipients.user_ids))).scalars().all()
            for user in users:
                prefs = notifications.get_prefs(db, user.id)
                if not kind_enabled(prefs.kinds if prefs else None, KIND):
                    continue
                if not platform_allowed(prefs, change.platform_code):
                    continue
                delivery = notifications.notify_user(
                    db,
                    user,
                    KIND,
                    title=title,
                    text=text,
                    dedupe_key=f"cancel:{recipients.identity_key}:{int(change.cancelled)}:{week_key}",
                    url=f"{base_url}/locations/{change.slug}",
                    url_label="Страница локации",
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
