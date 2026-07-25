"""Реестр sync-пайплайнов и постановка их в очередь.

Живёт на стороне backend (а не в процессе бота), чтобы бот не тянул граф
Celery-задач. Бот обращается сюда через internal API
(`/api/internal/bot/admin/sync-pipelines`, `/sync-enqueue`).
"""

from __future__ import annotations

from app.workers.tasks.five_verst_sync import (
    reconcile_stale_protocols_task,
    sync_latest_results_task,
    sync_location_rotation_task,
    sync_location_task,
    sync_locations_registry_task,
)
from app.workers.tasks.s95_sync import (
    s95_api_full_backfill_task,
    s95_api_sync_updated_task,
    s95_sync_locations_registry_task,
)

# S95 is JSON-API-only end to end (no HTML protocol scraper) — see
# app/sync/s95_protocol_api.py. The only HTML parsing left for S95 is the athlete
# profile page, which isn't part of this batch pipeline registry.
PIPELINES: dict[str, tuple[str, object, str]] = {
    "registry": ("5v registry /events/", sync_locations_registry_task, "five_verst"),
    "latest": ("5v latest /results/latest/", sync_latest_results_task, "five_verst"),
    "rotation": ("5v location rotation", sync_location_rotation_task, "five_verst"),
    "reconcile": ("5v reconcile protocols", reconcile_stale_protocols_task, "five_verst"),
    "s95-registry": ("s95 registry /activities", s95_sync_locations_registry_task, "s95"),
    "s95-sync-updated": ("s95 API: обновлённые протоколы", s95_api_sync_updated_task, "s95"),
    "s95-full-backfill": ("s95 API: полный backfill", s95_api_full_backfill_task, "s95"),
}

# Tasks whose signature has no `force` kwarg — enqueue with no kwargs at all.
_PIPELINES_WITHOUT_FORCE: frozenset[str] = frozenset({"s95-sync-updated", "s95-full-backfill"})


def list_pipelines() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, (label, _task, _queue) in sorted(PIPELINES.items())]


def enqueue_pipeline(name: str, *, location_slug: str | None = None) -> str:
    key = name.strip().lower()
    if key == "location":
        if not location_slug:
            raise ValueError("Укажите slug: /sync location <slug>")
        sync_location_task.apply_async(kwargs={"location_slug": location_slug}, queue="five_verst")
        return f"Поставлена в очередь: 5v location {location_slug}"

    item = PIPELINES.get(key)
    if item is None:
        known = ", ".join(sorted({*PIPELINES.keys(), "location"}))
        raise ValueError(f"Неизвестный пайплайн. Доступно: {known}")
    label, task, queue = item
    kwargs = {} if key in _PIPELINES_WITHOUT_FORCE else {"force": True}
    task.apply_async(kwargs=kwargs, queue=queue)  # type: ignore[attr-defined]
    return f"Поставлена в очередь: {label}"
