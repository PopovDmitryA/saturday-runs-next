"""Поминутное наблюдение за выгрузкой протоколов 5 вёрст.

Наследник легаси date_load_protocol.py (root-крон на проде, гасится после
двух суббот паритета): одна страница /results/latest/ за прогон, для каждой
пары (локация, дата), которой ещё нет в protocol_upload_facts, пишем момент
первого обнаружения. Задержку выгрузки потом считаем по формуле Дмитрия:
first_seen_at − (дата + местное время старта − tz-поправка + финиш последнего).

Отличия от легаси: локация ищется по слагу из ссылки (а не по русскому имени
через отдельную таблицу), время старта здесь НЕ парсится — оно живёт в
location_descriptions.schedule_parsed и обновляется синком реестра.

Прогонов ~1500 в сутки, поэтому в scheduled_run_logs пишем только прогоны с
новыми фактами или ошибкой — тихий «ничего нового» журнал бы утопил.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Event, EventSummary, Location, Platform, ProtocolUploadFact
from app.platform_adapters.five_verst import bulk_parser

logger = logging.getLogger(__name__)

# Отметка последнего успешного взгляда на страницу. Прогон идёт раз в минуту;
# если предыдущего взгляда не было или он старше этого окна, значит между ними
# зияет дыра, и «протокол появился только что» утверждать нельзя.
WATCH_HEARTBEAT_KEY = "five_verst:protocol_watch:last_seen_at"
WATCH_HEARTBEAT_TTL_SECONDS = 24 * 3600
# Порог непрерывности наблюдения — по дню недели по Москве (Дмитрий
# 03.09.2026): суббота ходит ежеминутно и разрыв длиннее получаса уже дыра;
# воскресенье — раз в 5 минут, будни — раз в 30, и там терпимы 2 и 5 часов.
# Разрыв длиннее порога означает: протокол увидели уже лежащим, момент
# появления неизвестен. Размер пачки признаком дыры НЕ считается — страница
# одна на все площадки, и десятки протоколов за минуту в субботу штатны.
MSK = timezone(timedelta(hours=3))
OBSERVATION_GAP_BY_WEEKDAY_SECONDS = {
    5: 30 * 60,  # суббота
    6: 2 * 3600,  # воскресенье
}
OBSERVATION_GAP_WEEKDAY_SECONDS = 5 * 3600


@dataclass
class ProtocolWatchResult:
    checked: int = 0
    new_facts: int = 0
    # Записаны при перерыве в наблюдении: протокол увидели уже лежащим, момент
    # появления неизвестен. В кабинете такие показываются прочерком.
    unconfirmed_facts: int = 0
    unknown_slugs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "new_facts": self.new_facts,
            "unconfirmed_facts": self.unconfirmed_facts,
            "unknown_slugs": self.unknown_slugs,
        }


def _previous_observation() -> datetime | None:
    """Когда мы в последний раз успешно смотрели на страницу."""
    raw = get_redis_client().get(WATCH_HEARTBEAT_KEY)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _mark_observed(now: datetime) -> None:
    """Отметка ставится только после успешного разбора страницы: пустая или
    сломанная страница наблюдением не считается."""
    get_redis_client().set(WATCH_HEARTBEAT_KEY, now.isoformat(), ex=WATCH_HEARTBEAT_TTL_SECONDS)


def max_observation_gap_seconds(now: datetime) -> int:
    weekday = now.astimezone(MSK).weekday()
    return OBSERVATION_GAP_BY_WEEKDAY_SECONDS.get(weekday, OBSERVATION_GAP_WEEKDAY_SECONDS)


def _observation_is_continuous(now: datetime, previous: datetime | None) -> bool:
    """Первый прогон 02.09.2026 записал 20 площадок разом, как будто все их
    протоколы появились в 21:00, — а они лежали там уже несколько дней. Факт
    подтверждён, только если предыдущий взгляд был недавно."""
    if previous is None:
        return False
    return (now - previous).total_seconds() <= max_observation_gap_seconds(now)


def earliest_db_sightings(
    db: Session, pairs: list[tuple[UUID, date]]
) -> dict[tuple[UUID, date], datetime]:
    """Когда протокол впервые попал в нашу базу другими путями.

    Часовой синк latest и загрузка протоколов заводят сводку и событие — и
    момент их создания доказывает, что протокол к тому времени уже лежал на
    сайте. Если это раньше, чем его увидел обходчик, выгрузкой считаем именно
    этот момент (Дмитрий 03.09.2026): так закрылись 20 площадок холодного
    старта, у Ставрополя вышло 29.08 09:00 вместо 02.09 21:00.
    """
    if not pairs:
        return {}
    location_ids = {location_id for location_id, _ in pairs}
    dates = {event_date for _, event_date in pairs}
    rows = (
        db.query(
            Event.location_id,
            Event.event_date,
            func.min(func.least(Event.created_at, func.coalesce(EventSummary.created_at, Event.created_at))),
        )
        .outerjoin(EventSummary, EventSummary.event_id == Event.id)
        .filter(Event.location_id.in_(location_ids), Event.event_date.in_(dates))
        .group_by(Event.location_id, Event.event_date)
        .all()
    )
    return {(location_id, event_date): seen for location_id, event_date, seen in rows if seen is not None}


def record_protocol_upload_facts(db: Session, *, source: str = "site") -> ProtocolWatchResult:
    """Снять /results/latest/ и дописать новые факты появления протоколов."""

    result = ProtocolWatchResult()
    summaries, _html = bulk_parser.fetch_latest_results()
    if not summaries:
        return result

    platform = db.query(Platform).filter(Platform.code == "five_verst").one()
    slugs = {summary.location_external_key for summary in summaries if summary.location_external_key}
    locations = {
        row.external_key: row
        for row in db.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key.in_(slugs))
        .all()
    }

    now = datetime.now(timezone.utc)
    previous = _previous_observation()
    candidates: list[tuple[str, UUID, date]] = []  # (slug, location_id, event_date)
    for summary in summaries:
        if summary.is_test_event:
            continue
        # Протокол не появляется раньше самого старта: дата из будущего — ошибка
        # разбора, факт по ней ставить нельзя.
        if summary.event_date > now.date():
            logger.warning(
                "protocol watch: дата %s из будущего у %s — пропуск",
                summary.event_date,
                summary.location_external_key,
            )
            continue
        location = locations.get(summary.location_external_key)
        if location is None:
            # Новая локация, которой ещё нет в каталоге: синк реестра подтянет
            # её вечером, факт запишется следующим прогоном после этого.
            result.unknown_slugs.append(summary.location_external_key)
            continue
        result.checked += 1
        candidates.append((summary.location_external_key, location.id, summary.event_date))

    if not candidates:
        return result

    # Прогон каждую минуту все выходные, и в устоявшемся состоянии ВСЕ факты
    # уже записаны. Один SELECT по датам страницы дешевле сотни холостых
    # INSERT ON CONFLICT (~140 тыс. пустых запросов за субботу+воскресенье).
    page_dates = {event_date for _slug, _location_id, event_date in candidates}
    existing = {
        (row.location_id, row.event_date)
        for row in db.query(ProtocolUploadFact.location_id, ProtocolUploadFact.event_date)
        .filter(ProtocolUploadFact.event_date.in_(page_dates))
        .all()
    }

    new_pairs = [
        (slug, location_id, event_date)
        for slug, location_id, event_date in candidates
        if (location_id, event_date) not in existing
    ]
    continuous = _observation_is_continuous(now, previous)
    db_seen = earliest_db_sightings(db, [(location_id, event_date) for _s, location_id, event_date in new_pairs])

    for slug, location_id, event_date in new_pairs:
        seen_in_db = db_seen.get((location_id, event_date))
        if seen_in_db is not None and seen_in_db < now:
            # База знала протокол раньше обходчика — этот момент точнее.
            first_seen_at, confirmed, fact_source = seen_in_db, True, "db"
        else:
            first_seen_at, confirmed, fact_source = now, continuous, source
        insert = (
            pg_insert(ProtocolUploadFact)
            .values(
                location_id=location_id,
                event_date=event_date,
                first_seen_at=first_seen_at,
                first_seen_confirmed=confirmed,
                source=fact_source,
            )
            # Гонка с параллельным прогоном всё ещё возможна — конфликт
            # по-прежнему глотаем, а не падаем. RETURNING отдаёт строку только
            # при реальной вставке: rowcount у psycopg3 здесь всегда -1.
            .on_conflict_do_nothing(constraint="uq_protocol_upload_facts_location_date")
            .returning(ProtocolUploadFact.id)
        )
        inserted = db.execute(insert).first() is not None
        if inserted:
            result.new_facts += 1
            if not confirmed:
                result.unconfirmed_facts += 1
            logger.info(
                "protocol watch: %s %s протокол %s в %s",
                slug,
                event_date.isoformat(),
                "известен базе с" if fact_source == "db" else ("замечен" if confirmed else "увиден уже лежащим"),
                first_seen_at.isoformat(),
            )
    if result.new_facts:
        db.flush()
    # Страница разобрана, факты записаны — вот теперь этот взгляд считается.
    _mark_observed(now)
    return result
