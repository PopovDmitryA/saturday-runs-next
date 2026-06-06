from __future__ import annotations

from app.workers.tasks.five_verst_sync import (
    reconcile_stale_protocols_task,
    sync_latest_results_task,
    sync_location_rotation_task,
    sync_location_task,
    sync_locations_registry_task,
)

PIPELINES: dict[str, tuple[str, object]] = {
    "registry": ("5v registry /events/", sync_locations_registry_task),
    "latest": ("5v latest /results/latest/", sync_latest_results_task),
    "rotation": ("5v location rotation", sync_location_rotation_task),
    "reconcile": ("5v reconcile protocols", reconcile_stale_protocols_task),
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
    label, task = item
    task.apply_async(queue="five_verst")
    return f"Поставлена в очередь: {label}"
