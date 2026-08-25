from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

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


def _summary_to_canonical(summary_row: EventSummary, location: Location) -> CanonicalEventSummary:
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


def _classify_reconcile_reason(
    summary_row: EventSummary,
    state: ProtocolSyncState | None,
    run_count: int,
    *,
    check_cutoff: datetime,
) -> ReconcileReason | None:
    del check_cutoff
    if state is None or state.last_protocol_check_at is None:
        return ReconcileReason.never_checked
    if summary_row.finishers_count is not None and state.finishers_at_fetch != summary_row.finishers_count:
        return ReconcileReason.summary_changed
    if summary_row.finishers_count is not None and run_count != summary_row.finishers_count:
        return ReconcileReason.count_mismatch
    return ReconcileReason.check_due


def plan_stale_protocol_reconcile(
    db: Session,
    *,
    limit: int = 100,
    min_check_interval_days: int = 0,
    location_slug: str | None = None,
) -> list[ReconcileCandidate]:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    run_counts = (
        db.query(RunResult.event_id, func.count(RunResult.id).label("run_count"))
        .group_by(RunResult.event_id)
        .subquery()
    )
    query = (
        db.query(EventSummary, Location, ProtocolSyncState, run_counts.c.run_count)
        .join(Event, EventSummary.event_id == Event.id)
        .join(Location, EventSummary.location_id == Location.id)
        .outerjoin(ProtocolSyncState, ProtocolSyncState.event_id == Event.id)
        .outerjoin(run_counts, run_counts.c.event_id == Event.id)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.event_id.isnot(None),
        )
    )
    if location_slug:
        query = query.filter(Location.external_key == location_slug)
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

    rows = query.order_by(ProtocolSyncState.last_protocol_check_at.asc().nullsfirst()).limit(limit).all()
    candidates: list[ReconcileCandidate] = []
    for summary_row, location, state, run_count in rows:
        reason = _classify_reconcile_reason(
            summary_row,
            state,
            int(run_count or 0),
            check_cutoff=datetime.now(timezone.utc),
        )
        candidates.append(
            ReconcileCandidate(
                external_event_key=summary_row.external_event_key,
                location_external_key=location.external_key,
                event_date=summary_row.event_date,
                reason=reason or ReconcileReason.check_due,
            )
        )
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
            summary = _summary_to_canonical(summary_row, location)
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
