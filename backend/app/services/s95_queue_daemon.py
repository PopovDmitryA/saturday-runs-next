"""Drain pending s95 rows in profile_fetch_pending from a machine that can reach s95.

Prod's IP is banned by s95, so s95 profile-link attempts that hit a cooldown get
enqueued into profile_fetch_pending but nothing ever processes them there (the prod
worker is banned, and the Mac parkrun daemon only handled parkrun rows). This module
adds the s95 side: it runs on the Mac (where s95.ru is reachable), creates the
PlatformLink for each queued row, then runs the s95 user sync to import runs.

Wired into the Mac daemon (`run_daemon`) so `make parkrun` drains both queues.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import SyncJobStatus, SyncJobTrigger
from app.parkrun.fetch.daemon_log import cline
from app.services.profile_fetch_pending_service import (
    count_pending_rows,
    describe_processed_profile,
    list_pending_rows,
    process_pending_row,
    reset_failed_pending,
)

logger = logging.getLogger(__name__)


def run_s95_pending_queue(
    db: Session,
    *,
    limit_pending: int = 50,
    reset_failed: bool = True,
) -> dict[str, object]:
    from app.s95.fetch.priority import s95_user_sync_context
    from app.sync.s95_user_sync import run_s95_user_sync

    if reset_failed:
        reset_failed_pending(db, "s95")

    summary: dict[str, int] = {}
    details: list[str] = []

    rows = list_pending_rows(db, platform_code="s95", limit=limit_pending)
    total = len(rows)
    backlog_total = count_pending_rows(db, "s95")

    for row in rows:
        label = row.external_user_id or row.profile_input[:40]
        user_id = row.user_id
        try:
            # The daemon does user-initiated sync work; mark it so the s95 fetch
            # coordinator does not cooperatively yield to the (local) batch queue.
            with s95_user_sync_context():
                outcome = process_pending_row(db, row)
                summary[outcome] = summary.get(outcome, 0) + 1
                details.append(f"{outcome}: s95 {label}")
                if outcome == "done":
                    description = describe_processed_profile(db, "s95", row.external_user_id)
                    if description:
                        cline(description)
                # activity_import rows already fetched and imported all data inside
                # process_pending_row — no additional sync needed. Only run the
                # full user sync for profile_preview rows (where process_pending_row
                # only created the PlatformLink but did not import run history).
                from app.models import ProfileFetchPendingOperation
                needs_sync = (
                    outcome == "done"
                    and user_id is not None
                    and row.operation != ProfileFetchPendingOperation.activity_import
                )
                if needs_sync:
                    job = run_s95_user_sync(db, user_id, SyncJobTrigger.linking)
                    key = (
                        "s95_sync_ok"
                        if job.status == SyncJobStatus.success
                        else "s95_sync_error"
                    )
                    summary[key] = summary.get(key, 0) + 1
                    details.append(f"{key}: s95 {label} ({job.error_message or 'ok'})")
        except Exception as exc:
            db.rollback()
            logger.exception("s95 queue row failed: %s", row.id)
            summary["error"] = summary.get("error", 0) + 1
            details.append(f"error: s95 {label} — {exc}")

    return {
        "summary": summary,
        "details": details,
        "total": total,
        "backlog_total": backlog_total,
    }
