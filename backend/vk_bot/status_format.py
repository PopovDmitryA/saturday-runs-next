from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.admin_pipeline_status_service import format_started_at


def _started_at_text(value: Any) -> str:
    if isinstance(value, datetime):
        return format_started_at(value)
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return format_started_at(datetime.fromisoformat(normalized))
        except ValueError:
            return value
    return format_started_at(None)


def format_pipeline_status(payload: dict[str, Any]) -> str:
    running = payload.get("running") or []
    queues = payload.get("queue_depths") or {}

    lines = ["📡 Статус пайплайнов", ""]

    if not running:
        lines.append("Сейчас ничего не выполняется.")
    else:
        lines.append(f"Запущено: {len(running)}")
        lines.append("")
        for index, item in enumerate(running, start=1):
            label = str(item.get("label") or "задача")
            started_at = item.get("started_at")
            started_text = _started_at_text(started_at)
            lines.append(f"{index}. {label}")
            lines.append(f"   {started_text}")

    queue_parts = [
        f"5verst протоколы: {queues.get('five_verst', 0)}",
        f"профили 5v: {queues.get('celery', 0)}",
        f"профили s95: {queues.get('s95', 0)}",
        f"профили parkrun: {queues.get('parkrun', 0)}",
    ]
    lines.extend(["", "Очереди: " + ", ".join(queue_parts)])
    return "\n".join(lines)
