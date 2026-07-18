from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import SyncJob, SyncJobStatus, SyncRun, SyncRunStatus
from app.services.celery_queue_inspector import (
    FIVE_VERST_BATCH_QUEUE,
    FIVE_VERST_USER_QUEUE,
    PARKRUN_SYNC_QUEUE,
    S95_BATCH_QUEUE,
    S95_USER_QUEUE,
    get_queue_length,
)
from app.services.parkrun_local_worker import get_local_worker_status
from app.services.sync_run_maintenance import close_stale_sync_runs
from app.workers.celery_app import celery_app

FIVE_VERST_QUEUE = FIVE_VERST_BATCH_QUEUE

PIPELINE_LAST_SUCCESS_TYPES: tuple[str, ...] = (
    "five_verst:latest",
    "five_verst:reconcile_protocols",
    "s95:latest",
    "s95:athletes_registry",
)

CELERY_TASK_LABELS: dict[str, str] = {
    "five_verst_sync.sync_locations_registry": "5verst: реестр /events/",
    "five_verst_sync.sync_latest_results": "5verst: свежие результаты /results/latest/",
    "five_verst_sync.sync_location_rotation": "5verst: ротация локаций",
    "five_verst_sync.reconcile_stale_protocols": "5verst: сверка протоколов",
    "s95_sync.sync_locations_registry": "s95: реестр /events",
    "s95_sync.api_new_protocols": "s95: новые протоколы (API)",
    "s95_sync.api_sync_updated": "s95: обновлённые протоколы (API)",
    "s95_sync.api_reconcile_date": "s95: сверка протоколов (API)",
    "s95_sync.api_full_backfill": "s95: полный backfill (API)",
    "five_verst_sync.sync_location": "5verst: локация",
    "five_verst_sync.sync_location_summaries": "5verst: своды локации",
    "user_sync.run_user_sync": "синхронизация профиля (5verst)",
    "s95_sync.run_s95_user_sync": "синхронизация профиля (с95)",
    "parkrun_sync.run_parkrun_user_sync": "синхронизация профиля (parkrun)",
}


def sync_run_type_label(sync_type: str) -> str:
    if sync_type == "locations_registry":
        return "5verst: реестр /events/"
    if sync_type == "five_verst:latest":
        return "5verst: свежие результаты /results/latest/"
    if sync_type == "five_verst:reconcile_protocols":
        return "5verst: сверка протоколов"
    if sync_type == "s95:registry":
        return "s95: реестр /events"
    if sync_type == "s95:latest":
        return "s95: свежие /activities"
    if sync_type == "s95:reconcile_protocols":
        return "s95: сверка протоколов"
    if sync_type == "s95:athletes_registry":
        return "s95: проверка профилей athletes"
    if sync_type.startswith("s95:location:"):
        return f"s95: локация {sync_type.removeprefix('s95:location:')}"
    if sync_type.startswith("five_verst:location:"):
        return f"5verst: локация {sync_type.removeprefix('five_verst:location:')}"
    if sync_type.startswith("five_verst:"):
        return sync_type.removeprefix("five_verst:")
    return sync_type


def celery_task_label(task_name: str | None) -> str:
    if not task_name:
        return "неизвестная задача"
    if task_name in CELERY_TASK_LABELS:
        return CELERY_TASK_LABELS[task_name]
    if task_name.startswith("five_verst_sync."):
        return task_name.removeprefix("five_verst_sync.").replace("_", " ")
    return task_name


def format_started_at(started_at: datetime | None, *, now: datetime | None = None) -> str:
    if started_at is None:
        return "время запуска неизвестно"
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - started_at).total_seconds()))
    clock = started_at.astimezone(timezone.utc).strftime("%H:%M UTC")
    if seconds < 60:
        return f"только что ({clock})"
    if seconds < 3600:
        return f"{seconds // 60} мин назад ({clock})"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours < 24:
        return f"{hours} ч {minutes} мин назад ({clock})"
    return clock


def _unix_to_datetime(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _inspect_active_celery_tasks() -> list[dict[str, Any]]:
    try:
        inspector = celery_app.control.inspect(timeout=1.0)
        if inspector is None:
            return []
        active = inspector.active() or {}
    except Exception:
        return []

    items: list[dict[str, Any]] = []
    for worker_name, tasks in active.items():
        for task in tasks:
            task_name = str(task.get("name") or "")
            started_at = _unix_to_datetime(task.get("time_start"))
            kwargs = task.get("kwargs") if isinstance(task.get("kwargs"), dict) else {}
            label = celery_task_label(task_name)
            if task_name == "five_verst_sync.sync_location" and kwargs.get("location_slug"):
                label = f"5verst: локация {kwargs['location_slug']}"
            elif task_name == "five_verst_sync.sync_location_summaries" and kwargs.get("location_slug"):
                label = f"5verst: своды {kwargs['location_slug']}"
            items.append(
                {
                    "source": "celery",
                    "label": label,
                    "task_name": task_name,
                    "task_id": task.get("id"),
                    "worker": worker_name,
                    "started_at": started_at,
                }
            )
    items.sort(key=lambda item: item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc))
    return items


def _last_successful_runs(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sync_type in PIPELINE_LAST_SUCCESS_TYPES:
        row = (
            db.query(SyncRun)
            .filter(
                SyncRun.sync_type == sync_type,
                SyncRun.status == SyncRunStatus.success,
            )
            .order_by(SyncRun.finished_at.desc().nullslast(), SyncRun.started_at.desc())
            .first()
        )
        if row is None:
            continue
        items.append(
            {
                "label": sync_run_type_label(sync_type),
                "sync_type": sync_type,
                "finished_at": row.finished_at or row.started_at,
            }
        )
    return items


def get_admin_pipeline_status(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    close_stale_sync_runs(db, now=now)

    sync_runs = (
        db.query(SyncRun)
        .filter(SyncRun.status == SyncRunStatus.running)
        .order_by(SyncRun.started_at.asc().nullsfirst())
        .all()
    )
    sync_jobs = (
        db.query(SyncJob)
        .filter(SyncJob.status == SyncJobStatus.running)
        .order_by(SyncJob.started_at.asc().nullsfirst())
        .all()
    )
    celery_tasks = _inspect_active_celery_tasks()

    running_items: list[dict[str, Any]] = []

    for run in sync_runs:
        running_items.append(
            {
                "source": "sync_run",
                "label": sync_run_type_label(run.sync_type),
                "started_at": run.started_at,
                "sync_run_id": str(run.id),
            }
        )

    for job in sync_jobs:
        running_items.append(
            {
                "source": "sync_job",
                "label": "синхронизация профиля пользователя",
                "started_at": job.started_at,
                "sync_job_id": str(job.id),
                "user_id": str(job.user_id),
            }
        )

    for task in celery_tasks:
        if any(
            item.get("source") == "sync_run"
            and item.get("label") == task.get("label")
            for item in running_items
        ):
            continue
        running_items.append(task)

    parkrun_worker = get_local_worker_status()
    worker_status = str(parkrun_worker.get("worker_status") or "idle")
    if worker_status in {"queued", "running"}:
        progress = parkrun_worker.get("worker_progress") or {}
        label = "parkrun: локальный воркер"
        if progress.get("current") and progress.get("total"):
            label = f"{label} ({progress['current']}/{progress['total']})"
        running_items.append(
            {
                "source": "parkrun_local_worker",
                "label": label,
                "started_at": _unix_to_datetime(parkrun_worker.get("worker_requested_at")),
                "worker_status": worker_status,
            }
        )

    running_items.sort(
        key=lambda item: item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc),
    )

    queue_depths = {
        FIVE_VERST_BATCH_QUEUE: get_queue_length(FIVE_VERST_BATCH_QUEUE),
        FIVE_VERST_USER_QUEUE: get_queue_length(FIVE_VERST_USER_QUEUE),
        S95_BATCH_QUEUE: get_queue_length(S95_BATCH_QUEUE),
        S95_USER_QUEUE: get_queue_length(S95_USER_QUEUE),
        PARKRUN_SYNC_QUEUE: get_queue_length(PARKRUN_SYNC_QUEUE),
    }

    return {
        "checked_at": now,
        "running": running_items,
        "last_success": _last_successful_runs(db),
        "queue_depths": queue_depths,
        "parkrun_local_worker": parkrun_worker,
    }
