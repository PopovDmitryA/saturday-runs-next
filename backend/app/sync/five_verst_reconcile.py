from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Query, Session
from sqlalchemy.sql import Subquery

from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.protocol_pause import wait_between_protocols
from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState, RunResult, SyncRun, SyncRunStatus
from app.platform_adapters.canonical import CanonicalEventSummary
from app.platform_adapters.five_verst.http import NotFoundError
from app.services.sync_report_labels import protocol_detail_label
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol, mark_protocol_check
from app.sync.iteration_commit import commit_step, mark_event_summary_error, persist_step_error, rollback_step

PLATFORM_CODE = "five_verst"


class ReconcileReason(str, Enum):
    never_checked = "never_checked"
    check_due = "check_due"
    count_mismatch = "count_mismatch"
    summary_changed = "summary_changed"
    # Протокол отстал от саммари по расписке summary_hash_at_fetch.
    protocol_debt = "protocol_debt"
    # Сводка обещает время лидера, которого в нашем протоколе нет.
    time_mismatch = "time_mismatch"


# Причины, ради которых кандидат идёт вне очереди и вне фильтра по давности.
# Общее у них одно: протокол, который мы держим, заведомо не тот, что на
# сайте. count_mismatch сюда НЕ входит намеренно — расхождение по количеству
# у 5 вёрст чаще всего структурное (схлопнутые дубли, «НЕИЗВЕСТНЫЙ» без
# времени), перекачкой не лечится, и таких строк под две сотни: в приоритете
# они встали бы навсегда и заслонили обычную ротацию.
PRIORITY_REASONS = (
    ReconcileReason.protocol_debt,
    ReconcileReason.summary_changed,
    ReconcileReason.time_mismatch,
)


@dataclass(frozen=True)
class ReconcileCandidate:
    external_event_key: str
    location_external_key: str
    event_date: object
    reason: ReconcileReason


@dataclass
class ReconcileProtocolsOptions:
    dry_run: bool = False
    limit: int = 10
    min_check_interval_days: int = 7
    location_slug: str | None = None
    # Не перекачивать протокол с расхождением чаще, чем раз в N часов (счёт от
    # последней ЗАКАЧКИ). Фильтр min_check_interval_days на расхождения не
    # действует вовсе, и без этого потолка неизлечимое расхождение
    # перечитывалось бы каждые три часа.
    mismatch_retry_interval_hours: int = 6


@dataclass
class ReconcileProtocolsResult:
    candidates_total: int = 0
    protocols_fetched: int = 0
    protocols_changed: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    planned: list[str] = field(default_factory=list)
    fetched_protocols: list[str] = field(default_factory=list)
    changed_protocols: list[str] = field(default_factory=list)
    # Протоколы, чьи страницы удалены с сайта (404). Это не сбой запуска:
    # они помечаются в event_summaries и уходят в конец очереди проверок,
    # а не валят прогон в статус error каждым циклом.
    pages_missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type="five_verst:reconcile_protocols",
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


def summary_to_canonical(summary_row: EventSummary, location: Location) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=summary_row.external_event_key,
        event_date=summary_row.event_date,
        event_number=summary_row.event_number,
        location_external_key=location.external_key,
        location_name=location.name,
        finishers_count=summary_row.finishers_count,
        volunteers_count=summary_row.volunteers_count,
        avg_time_sec=summary_row.avg_time_sec,
        avg_time_display=summary_row.avg_time_display,
        best_female_time_sec=summary_row.best_female_time_sec,
        best_female_time_display=summary_row.best_female_time_display,
        best_male_time_sec=summary_row.best_male_time_sec,
        best_male_time_display=summary_row.best_male_time_display,
        is_test_event=summary_row.is_test_event,
        source_url=summary_row.source_url or "",
        summary_hash=summary_row.summary_hash,
    )


def _claimed_fastest_sec(summary_row: EventSummary) -> int | None:
    """Время лидера, которое обещает сводка 5 вёрст.

    0 в best_male/best_female — не результат, а «в этом зачёте никого не было».
    """
    claimed = [
        value
        for value in (summary_row.best_male_time_sec, summary_row.best_female_time_sec)
        if value
    ]
    return min(claimed) if claimed else None


def _classify_reconcile_reason(
    summary_row: EventSummary,
    state: ProtocolSyncState | None,
    run_count: int,
    *,
    check_cutoff: datetime,
    fastest_stored_sec: int | None = None,
) -> ReconcileReason | None:
    del check_cutoff
    if state is None or state.last_protocol_check_at is None:
        return ReconcileReason.never_checked
    if state.summary_hash_at_fetch is not None and state.summary_hash_at_fetch != summary_row.summary_hash:
        return ReconcileReason.protocol_debt
    if summary_row.finishers_count is not None and state.finishers_at_fetch != summary_row.finishers_count:
        return ReconcileReason.summary_changed
    claimed_fastest = _claimed_fastest_sec(summary_row)
    if claimed_fastest is not None and fastest_stored_sec is not None and claimed_fastest != fastest_stored_sec:
        # Единственный признак, который видит правку времени при неизменном
        # числе финишёров. Именно так выглядел Серов 22.08.2026: 38 = 38,
        # а победитель разный.
        return ReconcileReason.time_mismatch
    if summary_row.finishers_count is not None and run_count != summary_row.finishers_count:
        return ReconcileReason.count_mismatch
    return ReconcileReason.check_due


def _protocol_aggregates(db: Session) -> Subquery:
    """Количество строк и лучшее время по каждому протоколу в нашей базе."""
    return (
        db.query(
            RunResult.event_id.label("event_id"),
            func.count(RunResult.id).label("run_count"),
            func.min(func.nullif(RunResult.finish_time_sec, 0)).label("fastest_sec"),
        )
        .group_by(RunResult.event_id)
        .subquery()
    )


def _candidates_query(
    db: Session,
    platform: Platform,
    location_slug: str | None,
) -> tuple[Query[Any], Subquery]:
    aggregates = _protocol_aggregates(db)
    query = (
        db.query(
            EventSummary,
            Location,
            ProtocolSyncState,
            aggregates.c.run_count,
            aggregates.c.fastest_sec,
        )
        .join(Event, EventSummary.event_id == Event.id)
        .join(Location, EventSummary.location_id == Location.id)
        .outerjoin(ProtocolSyncState, ProtocolSyncState.event_id == Event.id)
        .outerjoin(aggregates, aggregates.c.event_id == Event.id)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.event_id.isnot(None),
        )
    )
    if location_slug:
        query = query.filter(Location.external_key == location_slug)
    return query, aggregates


def _to_candidate(summary_row: EventSummary, location: Location, reason: ReconcileReason) -> ReconcileCandidate:
    return ReconcileCandidate(
        external_event_key=summary_row.external_event_key,
        location_external_key=location.external_key,
        event_date=summary_row.event_date,
        reason=reason,
    )


def _plan_mismatch_candidates(
    db: Session,
    platform: Platform,
    *,
    limit: int,
    location_slug: str | None,
    retry_interval_hours: int,
) -> list[ReconcileCandidate]:
    """Протоколы, про которые точно известно, что они не те, — вне очереди.

    Фильтр по давности сюда не применяется: расхождение, найденное сегодня, и
    перечитать надо сегодня, а не через `min_check_interval_days` дней. Именно
    этот фильтр держал Серова невидимым — его протокол «проверяли» в то же
    утро, когда качали.

    От бесконечного круга спасает `retry_interval_hours`, и считается он от
    момента ПОСЛЕДНЕЙ ЗАКАЧКИ, а не последней проверки. Это принципиально:
    расхождение у Серова появилось через 8 часов после того, как мы скачали
    протокол, — потолок «от проверки» спрятал бы ровно тот случай, ради
    которого всё и делается. Потолок «от закачки» отсекает другое: протокол,
    который мы только что перечитали, а он всё равно расходится (у 5 вёрст
    есть старты, где сводка вечно обещает не то число финишёров).
    """
    if limit <= 0:
        return []
    query, aggregates = _candidates_query(db, platform, location_slug)
    claimed_fastest = func.least(
        func.nullif(EventSummary.best_male_time_sec, 0),
        func.nullif(EventSummary.best_female_time_sec, 0),
    )
    query = query.filter(
        or_(
            and_(
                ProtocolSyncState.summary_hash_at_fetch.isnot(None),
                ProtocolSyncState.summary_hash_at_fetch != EventSummary.summary_hash,
            ),
            and_(
                EventSummary.finishers_count.isnot(None),
                ProtocolSyncState.finishers_at_fetch.isnot(None),
                ProtocolSyncState.finishers_at_fetch != EventSummary.finishers_count,
            ),
            and_(
                claimed_fastest.isnot(None),
                aggregates.c.fastest_sec.isnot(None),
                claimed_fastest != aggregates.c.fastest_sec,
            ),
        )
    )
    if retry_interval_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retry_interval_hours)
        query = query.filter(
            or_(
                ProtocolSyncState.last_protocol_fetched_at.is_(None),
                ProtocolSyncState.last_protocol_fetched_at < cutoff,
            )
        )

    # Свежие старты вперёд: сайту важнее правильный прошлый субботний протокол,
    # чем правильный протокол трёхлетней давности.
    rows = query.order_by(EventSummary.event_date.desc()).limit(limit).all()
    candidates: list[ReconcileCandidate] = []
    for summary_row, location, state, run_count, fastest_sec in rows:
        reason = _classify_reconcile_reason(
            summary_row,
            state,
            int(run_count or 0),
            check_cutoff=datetime.now(timezone.utc),
            fastest_stored_sec=int(fastest_sec) if fastest_sec is not None else None,
        )
        if reason not in PRIORITY_REASONS:
            # Строка попала в SQL-фильтр, но точную причину даёт классификатор
            # (например, state ещё ни разу не качали — это never_checked).
            # Такие отдаём обычной ротации, а не приоритету.
            continue
        candidates.append(_to_candidate(summary_row, location, reason))
    return candidates


def plan_stale_protocol_reconcile(
    db: Session,
    *,
    limit: int = 100,
    min_check_interval_days: int = 0,
    location_slug: str | None = None,
    mismatch_retry_interval_hours: int = 6,
) -> list[ReconcileCandidate]:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    priority = _plan_mismatch_candidates(
        db,
        platform,
        limit=limit,
        location_slug=location_slug,
        retry_interval_hours=mismatch_retry_interval_hours,
    )
    if len(priority) >= limit:
        return priority[:limit]

    already_planned = {item.external_event_key for item in priority}
    query, aggregates = _candidates_query(db, platform, location_slug)
    if min_check_interval_days > 0:
        # Протокол, проверенный недавно, не перечитываем: без этого фильтра
        # reconcile гонял всю историю (~2900 протоколов) по кругу каждые
        # ~4 дня — час работы воркера и ~100 страниц 5verst.ru каждые 3 часа
        # ради заведомо неизменных страниц.
        cutoff = datetime.now(timezone.utc) - timedelta(days=min_check_interval_days)
        query = query.filter(
            or_(
                ProtocolSyncState.last_protocol_check_at.is_(None),
                ProtocolSyncState.last_protocol_check_at < cutoff,
            )
        )

    if already_planned:
        query = query.filter(EventSummary.external_event_key.notin_(already_planned))

    rest_limit = limit - len(priority)
    rows = query.order_by(ProtocolSyncState.last_protocol_check_at.asc().nullsfirst()).limit(rest_limit).all()
    candidates = list(priority)
    for summary_row, location, state, run_count, fastest_sec in rows:
        reason = _classify_reconcile_reason(
            summary_row,
            state,
            int(run_count or 0),
            check_cutoff=datetime.now(timezone.utc),
            fastest_stored_sec=int(fastest_sec) if fastest_sec is not None else None,
        )
        candidates.append(_to_candidate(summary_row, location, reason or ReconcileReason.check_due))
    return candidates


def reconcile_stale_protocols(
    db: Session,
    options: ReconcileProtocolsOptions | None = None,
) -> ReconcileProtocolsResult:
    options = options or ReconcileProtocolsOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = ReconcileProtocolsResult()
    candidates = plan_stale_protocol_reconcile(
        db,
        limit=options.limit,
        min_check_interval_days=options.min_check_interval_days,
        location_slug=options.location_slug,
        mismatch_retry_interval_hours=options.mismatch_retry_interval_hours,
    )
    result.candidates_total = len(candidates)
    result.planned = [item.external_event_key for item in candidates]
    if options.dry_run:
        return result

    sync_run = _start_sync_run(db, platform)
    db.commit()
    try:
        for index, candidate in enumerate(candidates):
            summary_row = (
                db.query(EventSummary)
                .filter(
                    EventSummary.platform_id == platform.id,
                    EventSummary.external_event_key == candidate.external_event_key,
                )
                .one()
            )
            location = (
                db.query(Location)
                .filter(
                    Location.platform_id == platform.id,
                    Location.external_key == candidate.location_external_key,
                )
                .one()
            )
            summary = summary_to_canonical(summary_row, location)
            event_id = summary_row.event_id
            try:
                upsert_result = fetch_and_upsert_event_protocol(
                    db,
                    platform,
                    location,
                    summary,
                    summary_row,
                )
                result.protocols_fetched += 1
                label = protocol_detail_label(
                    location.external_key,
                    candidate.external_event_key,
                    location.name,
                )
                result.fetched_protocols.append(label)
                result.run_results_upserted += upsert_result.run_results_upserted
                result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
                if upsert_result.protocol_changed:
                    result.protocols_changed += 1
                    result.changed_protocols.append(label)
                # Коммит строго до паузы: сон с открытой транзакцией копится в
                # «idle in transaction», а прод рвёт такие сессии через 15 минут
                # (по этой причине падал каждый утренний reconcile в 06:10).
                commit_step(db)
                if index + 1 < len(candidates):
                    wait_between_protocols(reason="reconcile")
            except FiveVerstBanDetected as exc:
                # Кулдаун общий на все фетчи: остаток пачки гарантированно
                # упадёт с тем же «in cooldown». Раньше цикл шёл дальше и
                # печатал по 40-80 таких ошибок за прогон, а mark_protocol_check
                # помечал непроверенные протоколы проверенными.
                rollback_step(db)
                result.errors.append(f"{candidate.external_event_key}: {exc}; остаток пачки отложен")
                break
            except NotFoundError as exc:
                # Страница протокола удалена с сайта — известный факт, а не сбой:
                # раньше такие 404 (в пачках старых дат — до 88 за прогон)
                # засоряли errors и красили запуск в error. Помечаем summary,
                # двигаем отметку проверки — вернёмся к ним со следующим кругом
                # очереди, а не в каждом запуске.
                result.pages_missing.append(f"{candidate.external_event_key}: {exc}")

                def _apply_missing(
                    session: Session,
                    eid=event_id,
                    key=candidate.external_event_key,
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
                result.errors.append(f"{candidate.external_event_key}: {exc}")
                if event_id is not None:
                    persist_step_error(
                        db,
                        apply=lambda session, eid=event_id: mark_protocol_check(session, eid),
                    )
                else:
                    rollback_step(db)

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.candidates_total,
            upserted=result.protocols_fetched,
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        # sync_run закоммичен ещё до цикла — закрываем его же, а не плодим
        # второй failed-ран, оставляя первый висеть в running навсегда.
        _finish_sync_run(db, sync_run, success=False, fetched=0, upserted=0, error=str(exc))
        db.commit()
        raise
