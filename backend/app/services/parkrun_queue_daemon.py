from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    ProfileFetchPending,
)
from app.parkrun.fetch.daemon_session import (
    ParkrunDaemonSession,
    activate_daemon_session,
    deactivate_daemon_session,
)
from app.services.parkrun_local_worker import prepare_parkrun_cdp_fetch, sync_parkrun_runs_for_user
from app.services.profile_fetch_pending_service import (
    list_pending_rows,
    process_pending_row,
    requeue_stuck_done_parkrun_pending,
    reset_failed_parkrun_pending,
)
from app.sync.user_sync import _count_participant_runs

logger = logging.getLogger(__name__)

WorkKind = Literal["pending", "sync"]


@dataclass(frozen=True)
class ParkrunWorkItem:
    kind: WorkKind
    label: str
    user_id: UUID | None = None
    pending_id: UUID | None = None


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
    for link in links:
        if link.user_id in seen_sync_users:
            continue
        runs = 0
        if link.participant_id is not None:
            runs = _count_participant_runs(db, link.participant_id)
        needs_sync = (
            link.sync_status == PlatformLinkSyncStatus.error
            or runs == 0
        )
        if not needs_sync:
            continue
        items.append(
            ParkrunWorkItem(
                kind="sync",
                label=link.external_user_id,
                user_id=link.user_id,
            )
        )
        seen_sync_users.add(link.user_id)

    return items


def _process_pending_item(db: Session, item: ParkrunWorkItem) -> tuple[str, UUID | None]:
    if item.pending_id is None:
        return "error", None
    row = db.get(ProfileFetchPending, item.pending_id)
    if row is None:
        return "error", None
    outcome = process_pending_row(db, row)
    user_id = row.user_id if outcome == "done" else None
    return outcome, user_id


def run_parkrun_queue_daemon(
    db: Session,
    session: ParkrunDaemonSession,
    items: list[ParkrunWorkItem],
) -> dict[str, int | list[str]]:
    summary: dict[str, int] = {}
    details: list[str] = []
    total = len(items)

    for index, item in enumerate(items, start=1):
        session.show_status(f"{index}/{total}: {item.kind} {item.label}")
        try:
            if item.kind == "pending":
                outcome, user_id = _process_pending_item(db, item)
                summary[outcome] = summary.get(outcome, 0) + 1
                details.append(f"{outcome}: {item.label}")
                if outcome == "done" and user_id is not None:
                    sync_line = sync_parkrun_runs_for_user(db, user_id, label=item.label)
                    details.append(sync_line)
                    key = "sync_ok" if sync_line.startswith("sync_ok") else "sync_error"
                    summary[key] = summary.get(key, 0) + 1
            elif item.kind == "sync" and item.user_id is not None:
                sync_line = sync_parkrun_runs_for_user(db, item.user_id, label=item.label)
                details.append(sync_line)
                key = "sync_ok" if sync_line.startswith("sync_ok") else "sync_error"
                summary[key] = summary.get(key, 0) + 1
        except Exception as exc:
            logger.exception("parkrun queue item failed: %s", item.label)
            summary["error"] = summary.get("error", 0) + 1
            details.append(f"error: {item.label} — {exc}")

        if index < total:
            session.human_pause_between_jobs()

    return {"summary": summary, "details": details, "total": total}


def run_daemon(
    db: Session,
    *,
    use_cdp: bool | None = None,
    cdp_url: str | None = None,
    launch_chrome: bool = True,
    limit_pending: int = 50,
    include_sync: bool = True,
) -> dict[str, int | list[str]]:
    from app.config import get_settings

    prepare_parkrun_cdp_fetch()
    items = build_parkrun_work_queue(
        db,
        limit_pending=limit_pending,
        include_sync=include_sync,
    )
    if not items:
        print("Очередь parkrun пуста (pending и sync).", flush=True)
        return {"summary": {}, "details": [], "total": 0}

    settings = get_settings()
    cdp = use_cdp if use_cdp is not None else bool(
        settings.parkrun_use_cdp_for_fetch and settings.parkrun_cdp_url.strip()
    )
    mode = f"Chrome CDP ({cdp_url or settings.parkrun_cdp_url})" if cdp else "Playwright Chromium"
    print(f"В очереди: {len(items)} задач(и). Браузер: {mode}", flush=True)
    with ParkrunDaemonSession(
        use_cdp=use_cdp,
        cdp_url=cdp_url,
        launch_chrome=launch_chrome,
    ) as browser_session:
        token = activate_daemon_session(browser_session)
        try:
            return run_parkrun_queue_daemon(db, browser_session, items)
        finally:
            deactivate_daemon_session(token)
