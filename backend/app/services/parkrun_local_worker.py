from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.parkrun.fetch.captcha_state import clear_captcha_pending
from app.platform_fetch.cooldown import clear_platform_cooldown
from app.services.profile_fetch_pending_service import (
    list_pending_rows,
    process_pending_row,
    requeue_stuck_done_parkrun_pending,
    requeue_stuck_processing_parkrun_pending,
    reset_failed_parkrun_pending,
)

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "parkrun:local_worker:heartbeat"
STATUS_KEY = "parkrun:local_worker:status"
REQUESTED_AT_KEY = "parkrun:local_worker:requested_at"
PROGRESS_KEY = "parkrun:local_worker:progress"
LAST_HANDLED_KEY = "parkrun:local_worker:last_handled_at"
TTL_SECONDS = 3600


def _redis():
    return get_redis_client()


def touch_heartbeat() -> None:
    _redis().set(HEARTBEAT_KEY, str(time.time()), ex=TTL_SECONDS)


def is_worker_alive(*, max_age_seconds: float = 20.0) -> bool:
    raw = _redis().get(HEARTBEAT_KEY)
    if raw is None:
        return False
    try:
        return (time.time() - float(raw)) <= max_age_seconds
    except ValueError:
        return False


def get_local_worker_status() -> dict[str, Any]:
    redis = _redis()
    status = redis.get(STATUS_KEY) or "idle"
    progress_raw = redis.get(PROGRESS_KEY)
    progress: dict[str, Any] = {}
    if progress_raw:
        try:
            progress = json.loads(progress_raw)
        except json.JSONDecodeError:
            progress = {"raw": progress_raw}
    return {
        "worker_alive": is_worker_alive(),
        "worker_status": status,
        "worker_requested_at": redis.get(REQUESTED_AT_KEY),
        "worker_last_handled_at": redis.get(LAST_HANDLED_KEY),
        "worker_progress": progress,
    }


def request_local_worker_run(*, reset_failed: bool = True) -> dict[str, Any]:
    redis = _redis()
    if not is_worker_alive():
        return {
            "queued": False,
            "message": (
                "Локальный воркер не запущен. В терминале на Mac: "
                "cd ~/Projects/saturday_runs_stats && make parkrun-local-worker"
            ),
        }
    now = str(time.time())
    redis.set(REQUESTED_AT_KEY, now, ex=TTL_SECONDS)
    redis.set(STATUS_KEY, "queued", ex=TTL_SECONDS)
    redis.set(
        PROGRESS_KEY,
        json.dumps({"message": "Ожидание воркера…", "reset_failed": reset_failed}, ensure_ascii=False),
        ex=TTL_SECONDS,
    )
    return {
        "queued": True,
        "message": "Задача поставлена в очередь. Воркер на Mac начнёт обработку parkrun.",
        "requested_at": now,
    }


def prepare_parkrun_cdp_fetch() -> None:
    """Clear Redis ban/captcha pause so Mac CDP fetch can run after Docker failures."""
    clear_captcha_pending()
    clear_platform_cooldown("parkrun")


def sync_parkrun_runs_for_user(
    db: Session, user_id: UUID, *, label: str = "", reuse_fresh_seconds: float | None = None
) -> str:
    """Import parkrun runs via CDP (Mac worker); Docker Celery often cannot reach Chrome.

    reuse_fresh_seconds: если участник был загружен не позже указанного окна назад,
    user-sync переиспользует эти данные вместо повторного фетча (демон вызывает
    sync сразу после pending-импорта того же атлета).
    """
    from app.models import SyncJobTrigger
    from app.parkrun.fetch.daemon_session import get_active_daemon_session
    from app.sync.parkrun_user_sync import run_parkrun_user_sync
    from app.sync.user_sync import UserSyncError

    if not get_settings().parkrun_use_cdp_for_fetch and get_active_daemon_session() is None:
        return "sync_skipped: запустите make parkrun (браузер не активен)"

    prepare_parkrun_cdp_fetch()
    tag = label or str(user_id)
    try:
        job = run_parkrun_user_sync(
            db, user_id, SyncJobTrigger.linking, reuse_fresh_seconds=reuse_fresh_seconds
        )
    except UserSyncError as exc:
        return f"sync_error: {tag} — {exc}"
    except Exception as exc:
        logger.exception("parkrun CDP sync failed for user %s", user_id)
        return f"sync_error: {tag} — {exc}"

    if job.status.value == "success":
        return f"sync_ok: {tag}"
    err = (job.error_message or job.status.value)[:300]
    return f"sync_failed: {tag} — {err}"


def run_pending_parkrun_batch(db: Session, *, limit: int = 20, reset_failed: bool = True) -> dict[str, Any]:
    """Called by the Mac worker process (CDP fetch must be enabled via env)."""
    redis = _redis()
    redis.set(STATUS_KEY, "running", ex=TTL_SECONDS)
    requeued_failed = 0
    requeued_stuck_done = 0
    if reset_failed:
        requeued_failed = reset_failed_parkrun_pending(db)
        requeued_stuck_done = requeue_stuck_done_parkrun_pending(db)
        requeue_stuck_processing_parkrun_pending(db)
    prepare_parkrun_cdp_fetch()

    rows = list_pending_rows(db, platform_code="parkrun", limit=limit)
    summary: dict[str, int] = {}
    details: list[str] = []
    users_to_sync: dict[UUID, str] = {}
    for index, row in enumerate(rows, start=1):
        redis.set(
            PROGRESS_KEY,
            json.dumps(
                {
                    "current": index,
                    "total": len(rows),
                    "label": row.external_user_id or row.profile_input,
                    "details": details[-10:],
                },
                ensure_ascii=False,
            ),
            ex=TTL_SECONDS,
        )
        outcome = process_pending_row(db, row)
        db.refresh(row)
        summary[outcome] = summary.get(outcome, 0) + 1
        label = row.external_user_id or row.profile_input
        line = f"{outcome}: {label}"
        if row.last_error:
            line = f"{line} — {row.last_error[:300]}"
        details.append(line)
        if (
            outcome == "done"
            and row.platform_code == "parkrun"
            and row.user_id is not None
        ):
            users_to_sync[row.user_id] = label

    for user_id, label in users_to_sync.items():
        sync_line = sync_parkrun_runs_for_user(db, user_id, label=label)
        details.append(sync_line)
        if sync_line.startswith("sync_ok"):
            summary["sync_ok"] = summary.get("sync_ok", 0) + 1
        elif sync_line.startswith("sync_error") or sync_line.startswith("sync_failed"):
            summary["sync_error"] = summary.get("sync_error", 0) + 1

    final_status = "done" if not summary.get("error") and not summary.get("cooldown") else "error"
    payload = {
        "summary": summary,
        "details": details,
        "total": len(rows),
        "requeued_failed": requeued_failed,
        "requeued_stuck_done": requeued_stuck_done,
    }
    redis.set(STATUS_KEY, final_status, ex=TTL_SECONDS)
    redis.set(PROGRESS_KEY, json.dumps(payload, ensure_ascii=False), ex=TTL_SECONDS)
    redis.set(LAST_HANDLED_KEY, str(time.time()), ex=TTL_SECONDS)
    redis.delete(REQUESTED_AT_KEY)
    return payload


def should_process_requested_job() -> bool:
    redis = _redis()
    requested = redis.get(REQUESTED_AT_KEY)
    if requested is None:
        return False
    last_handled = redis.get(LAST_HANDLED_KEY)
    if last_handled is None:
        return True
    try:
        return float(requested) > float(last_handled)
    except ValueError:
        return True
