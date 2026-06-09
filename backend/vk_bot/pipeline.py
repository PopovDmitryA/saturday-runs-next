from __future__ import annotations

from app.workers.tasks.five_verst_sync import (
    reconcile_stale_protocols_task,
    sync_latest_results_task,
    sync_location_rotation_task,
    sync_location_task,
    sync_locations_registry_task,
)
from app.workers.tasks.s95_sync import (
    s95_reconcile_stale_protocols_task,
    s95_sync_athletes_registry_task,
    s95_sync_latest_task,
    s95_sync_location_rotation_task,
    s95_sync_locations_registry_task,
)

PIPELINES: dict[str, tuple[str, object, str]] = {
    "registry": ("5v registry /events/", sync_locations_registry_task, "five_verst"),
    "latest": ("5v latest /results/latest/", sync_latest_results_task, "five_verst"),
    "rotation": ("5v location rotation", sync_location_rotation_task, "five_verst"),
    "reconcile": ("5v reconcile protocols", reconcile_stale_protocols_task, "five_verst"),
    "s95-registry": ("s95 registry /activities", s95_sync_locations_registry_task, "s95"),
    "s95-latest": ("s95 latest /activities", s95_sync_latest_task, "s95"),
    "s95-rotation": ("s95 location rotation", s95_sync_location_rotation_task, "s95"),
    "s95-reconcile": ("s95 reconcile protocols", s95_reconcile_stale_protocols_task, "s95"),
    "s95-athletes": ("s95 athletes registry", s95_sync_athletes_registry_task, "s95"),
}


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
    task.apply_async(kwargs={"force": True}, queue=queue)
    return f"Поставлена в очередь: {label}"
