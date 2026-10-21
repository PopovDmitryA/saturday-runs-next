from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, Platform, SyncStatus
from app.services.location_activity_status import (
    INACTIVE_AFTER_DAYS,
    STATUS_OVERRIDE_INACTIVE,
    status_override_paused,
)
from app.services.location_catalog_cache import flush_location_catalog_caches

logger = logging.getLogger(__name__)

# Системы, у которых сводок стартов (event_summaries) нет вовсе: последний
# старт берём из самих протоколов. У RunPark сводок нет ни у одной из 51
# локации, и правило молча пропускало их все — Ньюпор Run 5К с последним
# стартом 18.05.2025 так и висел «действующим» (репорт 15.10.2026). parkrun
# сюда сознательно не входит: там тысячи исторических строк без сводок, их
# статус — отдельный вопрос.
LATEST_FROM_EVENTS_PLATFORMS = frozenset({"runpark"})

MOSCOW = ZoneInfo("Europe/Moscow")

# Оставлено ради старых вызовов снеделями: скрипт принимает недели, а порог живёт
# в днях рядом с остальной логикой статуса.
DEFAULT_INACTIVE_WEEKS = INACTIVE_AFTER_DAYS // 7


@dataclass
class MarkInactiveLocationsResult:
    locations_checked: int = 0
    locations_paused: int = 0
    locations_revived: int = 0
    locations_skipped_no_coords: int = 0
    # Ручных «не действует», снятых потому, что площадка снова провела старт.
    overrides_dropped: int = 0
    errors: list[str] = field(default_factory=list)


def mark_inactive_locations_paused(
    db: Session,
    *,
    inactive_days: int = INACTIVE_AFTER_DAYS,
    platform_codes: tuple[str, ...] | None = None,
    location_ids: list[UUID] | None = None,
    as_of: date | None = None,
) -> MarkInactiveLocationsResult:
    """Проставить «не действует» площадкам, молчащим дольше порога.

    Работает в обе стороны: площадка, вернувшаяся после долгого перерыва,
    снимает с себя статус сама (Бузулук зимовал 126 дней и вернулся). Не
    трогаем те, что помечены реестром системы вручную, — там заявление
    владельца, и оно сильнее нашей догадки по датам.

    Мимо правила проходят три случая: «скоро» (стартов ещё не было), строки без
    координат (разъездные серии — не место, а формат) и площадки без единого
    протокола (сравнивать не с чем).
    """
    result = MarkInactiveLocationsResult()
    cutoff = (as_of or date.today()) - timedelta(days=inactive_days)

    query = db.query(Location, Platform).join(Platform, Location.platform_id == Platform.id)
    if platform_codes:
        query = query.filter(Platform.code.in_(platform_codes))
    if location_ids is not None:
        # Пересчёт одной строки — после смены ручного статуса в админке.
        query = query.filter(Location.id.in_(location_ids))

    for location, platform in query.all():
        result.locations_checked += 1
        _drop_overridden_pause_after_start(db, location, result)
        manual = status_override_paused(location.status_override)
        if manual is not None:
            # Ручной статус админа сильнее догадки по датам — и в обе стороны.
            _set_paused(location, manual, result, reason="ручной статус")
            continue

        # Отмена ближайшего старта статуса не меняет: площадка работает, просто
        # в эту субботу не побегут.
        if location.is_upcoming:
            continue

        # Без координат площадки как места у нас нет — судить о её молчании
        # нечем. Так живут разъездные серии («С95 и друзья», «S95 & Friends»):
        # они не место, а формат, собираются нерегулярно, и «не действует»
        # после ста тихих дней было бы про них просто неправдой. На карту и в
        # каталог безкоординатные строки всё равно не попадают, зато страница
        # локации у них открывается — там эта плашка и была бы видна.
        if location.latitude is None or location.longitude is None:
            result.locations_skipped_no_coords += 1
            continue

        if platform.code in LATEST_FROM_EVENTS_PLATFORMS:
            latest_event_date = (
                db.query(func.max(Event.event_date))
                .filter(Event.location_id == location.id, Event.is_test_event.is_(False))
                .scalar()
            )
        else:
            latest_event_date = (
                db.query(func.max(EventSummary.event_date))
                # Тест №0 — не признак жизни площадки, как и везде в статистике.
                .filter(EventSummary.location_id == location.id, EventSummary.is_test_event.is_(False))
                .scalar()
            )
        if latest_event_date is None:
            continue

        # Пауза реестра сильнее дат: «(на паузе)» у 5 вёрст — заявление
        # владельца. Раньше правило снимало её у площадки со свежими стартами.
        _set_paused(
            location,
            location.registry_paused or latest_event_date < cutoff,
            result,
            reason="пауза в реестре" if location.registry_paused else f"последний старт {latest_event_date.isoformat()}",
        )

    db.flush()
    if result.locations_paused or result.locations_revived:
        flush_location_catalog_caches("правило молчания")
    return result


def _drop_overridden_pause_after_start(db: Session, location: Location, result: MarkInactiveLocationsResult) -> None:
    """Ручное «не действует» снимается само, если после него площадка провела старт.

    Решение Дмитрия 16.10.2026: админ ставит паузу переехавшей или закрытой
    площадке, но если она всё-таки побежала — значит, жива, и вручную
    поставленный статус не должен её прятать. «Действует» вручную не снимаем:
    старт его только подтверждает. Даты сравниваем по Москве: статус ставят
    из админки, старты записаны датами протоколов.
    """
    if location.status_override != STATUS_OVERRIDE_INACTIVE or location.status_override_at is None:
        return
    set_on = location.status_override_at.astimezone(MOSCOW).date()
    latest = (
        db.query(func.max(Event.event_date))
        .filter(Event.location_id == location.id, Event.is_test_event.is_(False))
        .scalar()
    )
    if latest is None or latest <= set_on:
        return
    location.status_override = None
    location.status_override_at = None
    result.overrides_dropped += 1
    logger.info(
        "Ручное «не действует» снято: %s провела старт %s после %s",
        location.external_key,
        latest.isoformat(),
        set_on.isoformat(),
    )


def _set_paused(location: Location, should_pause: bool, result: MarkInactiveLocationsResult, *, reason: str) -> None:
    if should_pause == location.is_paused:
        return
    location.is_paused = should_pause
    location.fetched_at = datetime.now(timezone.utc)
    location.sync_status = SyncStatus.ok
    if should_pause:
        result.locations_paused += 1
        logger.info("Локация ушла в «не действует»: %s (%s)", location.external_key, reason)
    else:
        result.locations_revived += 1
        logger.info("Локация вернулась в строй: %s (%s)", location.external_key, reason)
