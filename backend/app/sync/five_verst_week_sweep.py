"""Обход протоколов недели: третья страховка легаси-схемы.

Страховок в схеме Дмитрия три, и они ловят разное:

1. `five_verst_latest` — сводка страницы /results/latest/. Ловит изменения
   ТЕКУЩЕЙ недели: как только на странице поменялось хоть одно поле (число
   волонтёров, среднее время), протокол перекачивается. Слабое место —
   страница показывает только ПОСЛЕДНИЙ старт каждой площадки: как наступает
   новая суббота, прошлая с неё пропадает.
2. `five_verst_location_rotation` — сводка /results/all/ по локации, круг
   около недели. Ловит поздние правки прошедших стартов, которые видны в
   таблице локации.
3. Этот модуль — обход самих протоколов недели, без оглядки на сводку.
   Единственная страховка от правки, которая в сводке НЕ ОТРАЗИЛАСЬ: сводка
   знает только число финишёров, число волонтёров и три времени, а внутри
   протокола меняются роли волонтёров, привязки к атлетам, имена, позиции.
   Ни сверка (five_verst_reconcile), ни ротация такого не увидят: обе
   сравнивают наш протокол с НАШЕЙ же сводкой.

График (расписание в celery_app.py) повторяет легаси: понедельник и четверг —
последняя суббота W, среда — W-1, пятница — W-2. Два взгляда на W закрывают
догрузки, которые приезжают в начале недели.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.protocol_pause import wait_between_protocols
from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState, SyncRun, SyncRunStatus
from app.platform_adapters.five_verst.http import NotFoundError
from app.services.sync_report_labels import protocol_detail_label
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol, mark_protocol_check
from app.sync.five_verst_reconcile import summary_to_canonical
from app.sync.iteration_commit import (
    commit_step,
    mark_event_summary_error,
    persist_step_error,
    persist_summary_error,
    rollback_step,
)

PLATFORM_CODE = "five_verst"
logger = logging.getLogger(__name__)

# Неделя 5 вёрст считается от субботы: старт-перенос (1 января, спецзабег)
# попадает в ту же неделю, что и суббота, за которую он идёт.
SATURDAY = 5


@dataclass
class WeekSweepOptions:
    # 0 — последняя суббота, 1 — та, что перед ней, и так далее.
    weeks_back: int = 0
    dry_run: bool = False
    limit: int = 60
    # Протокол, скачанный недавно, в этот заход не берём: так цепочка звеньев
    # двигается вперёд, а повторный запуск в тот же день не качает всё заново.
    min_refetch_interval_hours: int = 12
    today: date | None = None


@dataclass
class WeekSweepResult:
    week_start: str = ""
    week_end: str = ""
    candidates_total: int = 0
    protocols_fetched: int = 0
    protocols_changed: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    planned: list[str] = field(default_factory=list)
    changed_protocols: list[str] = field(default_factory=list)
    pages_missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def week_window(weeks_back: int, *, today: date | None = None) -> tuple[date, date]:
    """Суббота недели W-`weeks_back` и последний день этой недели (пятница)."""
    today = today or datetime.now(timezone.utc).date()
    # Ближайшая суббота не позже сегодня: в понедельник это позавчерашняя,
    # в саму субботу — сегодняшняя.
    last_saturday = today - timedelta(days=(today.weekday() - SATURDAY) % 7)
    start = last_saturday - timedelta(weeks=weeks_back)
    return start, start + timedelta(days=6)


def plan_week_sweep(db: Session, options: WeekSweepOptions) -> list[tuple[EventSummary, Location]]:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    start, end = week_window(options.weeks_back, today=options.today)
    query = (
        db.query(EventSummary, Location)
        .join(Event, EventSummary.event_id == Event.id)
        .join(Location, EventSummary.location_id == Location.id)
        .outerjoin(ProtocolSyncState, ProtocolSyncState.event_id == Event.id)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.event_id.isnot(None),
            EventSummary.event_date >= start,
            EventSummary.event_date <= end,
        )
    )
    if options.min_refetch_interval_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=options.min_refetch_interval_hours)
        query = query.filter(
            or_(
                ProtocolSyncState.last_protocol_fetched_at.is_(None),
                ProtocolSyncState.last_protocol_fetched_at < cutoff,
            )
        )
    # Самые давно не перечитанные — вперёд: так звенья цепочки разбирают
    # неделю без повторов, даже если между ними протокол успел обновиться.
    return (
        query.order_by(ProtocolSyncState.last_protocol_fetched_at.asc().nullsfirst())
        .limit(options.limit)
        .all()
    )


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="five_verst:week_sweep",
        status=SyncRunStatus.running,
        parser_version=upsert.PARSER_VERSION,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _finish_sync_run(
    db: Session,
    run: SyncRun,
    *,
    success: bool,
    fetched: int,
    upserted: int,
    error: str | None = None,
) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = fetched
    run.records_upserted = upserted
    run.error_message = error
    db.flush()


def sweep_week_protocols(db: Session, options: WeekSweepOptions | None = None) -> WeekSweepResult:
    options = options or WeekSweepOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    start, end = week_window(options.weeks_back, today=options.today)
    result = WeekSweepResult(week_start=start.isoformat(), week_end=end.isoformat())

    candidates = plan_week_sweep(db, options)
    result.candidates_total = len(candidates)
    result.planned = [
        protocol_detail_label(location.external_key, summary.external_event_key, location.name)
        for summary, location in candidates
    ]
    if options.dry_run or not candidates:
        return result

    sync_run = _start_sync_run(db, platform)
    db.commit()
    try:
        for index, (summary_row, location) in enumerate(candidates):
            label = protocol_detail_label(
                location.external_key, summary_row.external_event_key, location.name
            )
            summary = summary_to_canonical(summary_row, location)
            try:
                upsert_result = fetch_and_upsert_event_protocol(
                    db,
                    platform,
                    location,
                    summary,
                    summary_row,
                )
                result.protocols_fetched += 1
                result.run_results_upserted += upsert_result.run_results_upserted
                result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
                if upsert_result.protocol_changed:
                    result.protocols_changed += 1
                    result.changed_protocols.append(label)
                commit_step(db)
            except FiveVerstBanDetected as exc:
                # Кулдаун общий на все фетчи: остаток очереди упал бы с той же
                # ошибкой, его заберёт следующий заход.
                rollback_step(db)
                result.errors.append(f"{label}: {exc}; остаток очереди отложен")
                break
            except NotFoundError as exc:
                # Страница протокола удалена с сайта — известный факт, а не сбой
                # прогона (иначе каждый обход красился бы в error из-за пары
                # старых дат). Двигаем отметку проверки и идём дальше.
                result.pages_missing.append(f"{label}: {exc}")

                def _apply_missing(
                    session: Session,
                    eid=summary_row.event_id,
                    key=summary_row.external_event_key,
                    message=str(exc),
                ) -> None:
                    if eid is not None:
                        mark_protocol_check(session, eid)
                    mark_event_summary_error(
                        session,
                        platform_id=platform.id,
                        external_event_key=key,
                        message=message,
                    )

                persist_step_error(db, apply=_apply_missing)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{label}: {exc}")
                persist_summary_error(
                    db,
                    platform_id=platform.id,
                    external_event_key=summary_row.external_event_key,
                    message=str(exc),
                )
            if index + 1 < len(candidates):
                wait_between_protocols(reason="week_sweep")

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.protocols_fetched,
            upserted=result.run_results_upserted + result.volunteer_results_upserted,
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        _finish_sync_run(db, sync_run, success=False, fetched=result.protocols_fetched, upserted=0, error=str(exc))
        db.commit()
        raise
