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
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Location, Platform, ProtocolUploadFact
from app.platform_adapters.five_verst import bulk_parser

logger = logging.getLogger(__name__)


@dataclass
class ProtocolWatchResult:
    checked: int = 0
    new_facts: int = 0
    unknown_slugs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "new_facts": self.new_facts,
            "unknown_slugs": self.unknown_slugs,
        }


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
    candidates: list[tuple[str, UUID, date]] = []  # (slug, location_id, event_date)
    for summary in summaries:
        if summary.is_test_event:
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

    for slug, location_id, event_date in candidates:
        if (location_id, event_date) in existing:
            continue
        insert = (
            pg_insert(ProtocolUploadFact)
            .values(
                location_id=location_id,
                event_date=event_date,
                first_seen_at=now,
                source=source,
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
            logger.info(
                "protocol watch: %s %s протокол замечен в %s",
                slug,
                event_date.isoformat(),
                now.isoformat(),
            )
    if result.new_facts:
        db.flush()
    return result
