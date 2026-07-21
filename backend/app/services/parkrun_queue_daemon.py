from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import (
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    ProfileFetchPending,
    SyncJob,
    SyncJobTrigger,
)
from app.parkrun.fetch.daemon_session import (
    ParkrunDaemonSession,
    activate_daemon_session,
    deactivate_daemon_session,
)
from app.services.parkrun_local_worker import prepare_parkrun_cdp_fetch, sync_parkrun_runs_for_user
from app.services.profile_fetch_pending_service import (
    count_pending_rows,
    describe_processed_profile,
    list_pending_rows,
    process_pending_row,
    requeue_stuck_done_parkrun_pending,
    requeue_stuck_processing_parkrun_pending,
    reset_failed_parkrun_pending,
)

logger = logging.getLogger(__name__)

WorkKind = Literal["pending", "sync"]


@dataclass(frozen=True)
class ParkrunWorkItem:
    kind: WorkKind
    label: str
    user_id: UUID | None = None
    pending_id: UUID | None = None


def _requeue_stuck_syncing_parkrun_links(db: Session) -> int:
    """Линки, зависшие в 'syncing' (краш/обрыв во время user-sync), очередь не
    подхватывает — build_parkrun_work_queue берёт только 'error'. Сбрасываем в
    'error', чтобы sync переотработался. Демон не работает конкурентно, поэтому
    любой 'syncing' на старте прогона — устаревший.
    """
    parkrun = db.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        return 0
    rows = (
        db.query(PlatformLink)
        .filter(
            PlatformLink.platform_id == parkrun.id,
            PlatformLink.sync_status == PlatformLinkSyncStatus.syncing,
        )
        .all()
    )
    for link in rows:
        link.sync_status = PlatformLinkSyncStatus.error
        if not link.error_message:
            link.error_message = "reset from stuck 'syncing' state"
    if rows:
        db.commit()
    return len(rows)


def build_parkrun_work_queue(
    db: Session,
    *,
    limit_pending: int = 50,
    include_sync: bool = True,
    reset_failed: bool = True,
) -> list[ParkrunWorkItem]:
    if reset_failed:
        reset_failed_parkrun_pending(db)
        requeue_stuck_done_parkrun_pending(db)
        requeue_stuck_processing_parkrun_pending(db)
        _requeue_stuck_syncing_parkrun_links(db)

    items: list[ParkrunWorkItem] = []
    seen_sync_users: set[UUID] = set()

    for row in list_pending_rows(db, platform_code="parkrun", limit=limit_pending):
        label = row.external_user_id or row.profile_input[:40]
        items.append(
            ParkrunWorkItem(
                kind="pending",
                label=label,
                user_id=row.user_id,
                pending_id=row.id,
            )
        )
        if row.user_id is not None:
            seen_sync_users.add(row.user_id)

    if not include_sync:
        return items

    parkrun = db.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        return items

    links = (
        db.query(PlatformLink)
        .filter(PlatformLink.platform_id == parkrun.id)
        .all()
    )
    error_links = [
        link
        for link in links
        if link.user_id not in seen_sync_users
        and link.sync_status == PlatformLinkSyncStatus.error
    ]
    manual_link_ids = _manual_sync_link_ids(db, [link.id for link in error_links])

    manual_items: list[ParkrunWorkItem] = []
    other_items: list[ParkrunWorkItem] = []
    for link in error_links:
        item = ParkrunWorkItem(kind="sync", label=link.external_user_id, user_id=link.user_id)
        (manual_items if link.id in manual_link_ids else other_items).append(item)
        seen_sync_users.add(link.user_id)

    # Ручной клик "Обновить" на сайте — это несколько штук, а не backlog;
    # такие запросы идут первыми, впереди даже "pending", а не в хвосте
    # общей очереди вместе с фоновыми/логин-триггерными ошибками синка.
    return manual_items + items + other_items


def _manual_sync_link_ids(db: Session, link_ids: list[UUID]) -> set[UUID]:
    """PlatformLink id → True, если ПОСЛЕДНИЙ SyncJob по нему — ручной клик.

    "Последний" определяется как первая по (link_id, created_at DESC) строка
    на link_id — обычный трюк "greatest-n-per-group" без window-функций.
    """
    if not link_ids:
        return set()
    rows = (
        db.query(SyncJob.platform_link_id, SyncJob.trigger)
        .filter(SyncJob.platform_link_id.in_(link_ids))
        .order_by(SyncJob.platform_link_id, SyncJob.created_at.desc())
        .all()
    )
    manual_ids: set[UUID] = set()
    seen: set[UUID] = set()
    for link_id, trigger in rows:
        if link_id in seen:
            continue
        seen.add(link_id)
        if trigger == SyncJobTrigger.manual:
            manual_ids.add(link_id)
    return manual_ids


# Сколько подряд обрывов коннекта к прод-БД терпим, прежде чем остановить
# прогон. Туннель к прод-Postgres (SSH, порт 5434) иногда рвётся посреди
# длинного прогона — дальше каждый профиль падает на первом же db.get, и нет
# смысла молотить остаток очереди гигантскими трейсбэками.
MAX_DB_CONNECTION_FAILURES = 3


def _is_db_connection_lost(exc: BaseException) -> bool:
    """True, если ошибка — потеря соединения с БД (оборванный SSH-туннель),
    а не обычный сбой фетча parkrun. Проверяем всю цепочку __cause__:
    sqlalchemy заворачивает psycopg.OperationalError (connection refused,
    SSL EOF, server closed connection) именно в OperationalError."""
    cursor: BaseException | None = exc
    for _ in range(10):
        if cursor is None:
            break
        if isinstance(cursor, OperationalError):
            return True
        cursor = cursor.__cause__
    return False


def _process_pending_item(db: Session, item: ParkrunWorkItem) -> tuple[str, UUID | None, str | None]:
    if item.pending_id is None:
        return "error", None, None
    row = db.get(ProfileFetchPending, item.pending_id)
    if row is None:
        return "error", None, None
    outcome = process_pending_row(db, row)
    user_id = row.user_id if outcome == "done" else None
    external_user_id = row.external_user_id if outcome == "done" else None
    return outcome, user_id, external_user_id


def run_parkrun_queue_daemon(
    db: Session,
    session: ParkrunDaemonSession,
    items: list[ParkrunWorkItem],
    after_item: Callable[[], str | None] | None = None,
) -> dict[str, object]:
    summary: dict[str, int] = {}
    details: list[str] = []
    total = len(items)
    db_connection_failures = 0

    for index, item in enumerate(items, start=1):
        session.show_status(f"{index}/{total}: {item.kind} {item.label}")
        try:
            if item.kind == "pending":
                outcome, user_id, external_user_id = _process_pending_item(db, item)
                summary[outcome] = summary.get(outcome, 0) + 1
                details.append(f"{outcome}: {item.label}")
                if outcome == "done":
                    description = describe_processed_profile(db, "parkrun", external_user_id)
                    if description:
                        session.show_status(description)
                if outcome == "done" and user_id is not None:
                    # Импорт только что стянул страницы атлета — user-sync
                    # переиспользует их (в пределах окна) вместо второго фетча.
                    sync_line = sync_parkrun_runs_for_user(
                        db, user_id, label=item.label, reuse_fresh_seconds=600
                    )
                    details.append(sync_line)
                    key = "sync_ok" if sync_line.startswith("sync_ok") else "sync_error"
                    summary[key] = summary.get(key, 0) + 1
            elif item.kind == "sync" and item.user_id is not None:
                description = describe_processed_profile(db, "parkrun", item.label)
                if description:
                    session.show_status(description)
                sync_line = sync_parkrun_runs_for_user(db, item.user_id, label=item.label)
                details.append(sync_line)
                key = "sync_ok" if sync_line.startswith("sync_ok") else "sync_error"
                summary[key] = summary.get(key, 0) + 1
            db_connection_failures = 0  # успех — сбрасываем счётчик обрывов
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass  # rollback на мёртвом коннекте сам падает — не мешаем
            logger.exception("parkrun queue item failed: %s", item.label)
            summary["error"] = summary.get("error", 0) + 1
            details.append(f"error: {item.label} — {exc}")

            if _is_db_connection_lost(exc):
                db_connection_failures += 1
                if db_connection_failures >= MAX_DB_CONNECTION_FAILURES:
                    summary["db_connection_lost"] = 1
                    print(
                        f"[parkrun queue] ОСТАНОВКА: {MAX_DB_CONNECTION_FAILURES} "
                        f"обрыва коннекта к прод-БД подряд (SSH-туннель, порт "
                        f"5434). Прогон прерван на {index}/{total}. Перезапусти "
                        f"make parkrun — туннель поднимется заново.",
                        flush=True,
                    )
                    break
            else:
                db_connection_failures = 0  # обычная ошибка фетча — не в счёт

        if getattr(session, "httpx_aborted", False):
            # --no-browser словил защиту WAF — дальше пачку не гоняем, нет
            # смысла добивать оставшиеся N строк той же блокировкой.
            print(
                f"[parkrun queue] --no-browser: обнаружена защита, "
                f"эксперимент остановлен на {index}/{total}.",
                flush=True,
            )
            break

        # Чередование очередей: после каждой задачи сайта — один шаг
        # parkrun-monitoring (eventhistory-саммари одной локации). Пауза
        # ставится ДО локации и ПОСЛЕ — иначе получалось "человек → сразу
        # локация → пауза → следующий человек", без паузы между человеком
        # и локацией вообще (нашли по факту наблюдения за логами).
        if after_item is not None:
            session.human_pause_between_jobs()
            line = after_item()
            if line:
                details.append(line)
                print("  ", line, flush=True)

        if index < total:
            session.human_pause_between_jobs()

    return {"summary": summary, "details": details, "total": total}


def _merge_results(*results: Mapping[str, object]) -> dict[str, object]:
    summary: dict[str, int] = {}
    details: list[str] = []
    total = 0
    for res in results:
        res_summary = cast("dict[str, int]", res.get("summary") or {})
        for key, value in res_summary.items():
            summary[key] = summary.get(key, 0) + value
        details.extend(cast("list[str]", res.get("details") or []))
        total += cast("int", res.get("total") or 0)
    return {"summary": summary, "details": details, "total": total}


def run_daemon(
    db: Session,
    *,
    use_cdp: bool | None = None,
    cdp_url: str | None = None,
    launch_chrome: bool = True,
    limit_pending: int = 50,
    include_sync: bool = True,
    use_httpx: bool = False,
    fast_delay_seconds: float | None = None,
) -> dict[str, object]:
    from app.config import get_settings
    from app.services.parkrun_monitoring_bridge import (
        monitoring_history_step,
        push_monitoring_to_server,
        refresh_monitoring_stats,
    )
    from app.services.s95_queue_daemon import run_s95_pending_queue

    prepare_parkrun_cdp_fetch()

    # parkrun-monitoring: сперва обновляем каталог + недельную статистику
    # стран (results-service доступен с Mac, но не с сервера).
    stats_line = refresh_monitoring_stats()
    if stats_line:
        print(stats_line, flush=True)

    # s95 uses its own fetch (no parkrun browser needed) — drain it first.
    s95_result = run_s95_pending_queue(db, limit_pending=limit_pending)
    if s95_result["total"]:
        print(
            f"s95 очередь: {s95_result['backlog_total']} задач(и) всего — "
            f"берём в этот прогон {s95_result['total']}, {s95_result['summary']}",
            flush=True,
        )
        for line in cast("list[str]", s95_result["details"]):
            print("  ", line, flush=True)

    items = build_parkrun_work_queue(
        db,
        limit_pending=limit_pending,
        include_sync=include_sync,
    )

    # Бюджет parkrun-monitoring на этот прогон: LIMIT шагов-саммари всего,
    # по одному после каждой задачи сайта, остаток — после очереди.
    monitoring_steps = 0

    def _monitoring_after_item() -> str | None:
        nonlocal monitoring_steps
        if monitoring_steps >= limit_pending:
            return None
        monitoring_steps += 1
        return monitoring_history_step(limit=1)

    if not items:
        print("Очередь parkrun пуста (pending и sync).", flush=True)
        parkrun_result: dict[str, object] = {"summary": {}, "details": [], "total": 0}
    else:
        settings = get_settings()
        cdp = use_cdp if use_cdp is not None else bool(
            settings.parkrun_use_cdp_for_fetch and settings.parkrun_cdp_url.strip()
        )
        if use_httpx:
            mode = "httpx БЕЗ БРАУЗЕРА (--no-browser, эксперимент)"
        elif cdp:
            mode = f"Chrome CDP ({cdp_url or settings.parkrun_cdp_url})"
        else:
            mode = "Playwright Chromium"
        # "sync" в items уже без ограничения limit_pending (см. build_parkrun_work_queue),
        # так что len(items)-pending_taken — это и есть точный размер той части
        # backlog'а. "pending" же обрезан лимитом — сравниваем с истинным total.
        pending_taken = sum(1 for i in items if i.kind == "pending")
        sync_taken = len(items) - pending_taken
        pending_total = count_pending_rows(db, "parkrun")
        backlog_total = pending_total + sync_taken
        print(
            f"В очереди: {backlog_total} задач(и) всего "
            f"(pending {pending_total}, sync {sync_taken}) — "
            f"берём в этот прогон {len(items)}. Браузер: {mode}",
            flush=True,
        )
        with ParkrunDaemonSession(
            use_cdp=use_cdp,
            cdp_url=cdp_url,
            launch_chrome=launch_chrome,
            use_httpx=use_httpx,
            fast_delay_seconds=fast_delay_seconds,
        ) as browser_session:
            token = activate_daemon_session(browser_session)
            try:
                parkrun_result = run_parkrun_queue_daemon(
                    db, browser_session, items, after_item=_monitoring_after_item
                )
            finally:
                deactivate_daemon_session(token)

    # Обрыв туннеля к прод-БД — не добираем остаток локаций (прогон прерван),
    # но push уже собранного делаем: он идёт по своему SSH-коннекту к серверу,
    # прод-туннель ему не нужен.
    db_lost = bool(cast("dict[str, int]", parkrun_result.get("summary") or {}).get(
        "db_connection_lost"
    ))
    if not db_lost:
        # Очередь сайта кончилась — добираем оставшийся бюджет саммари одним заходом.
        remaining = limit_pending - monitoring_steps
        if remaining > 0:
            drain_line = monitoring_history_step(limit=remaining)
            if drain_line:
                print(drain_line, flush=True)

    push_line = push_monitoring_to_server()
    if push_line:
        print(push_line, flush=True)

    return _merge_results(s95_result, parkrun_result)
