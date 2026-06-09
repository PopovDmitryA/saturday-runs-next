from __future__ import annotations

from datetime import datetime, timezone

from app.services.admin_pipeline_status_service import format_started_at, sync_run_type_label
from vk_bot.status_format import format_pipeline_status


def test_sync_run_type_label() -> None:
    assert sync_run_type_label("locations_registry") == "5verst: реестр /events/"
    assert sync_run_type_label("five_verst:location:kopyttse") == "5verst: локация kopyttse"


def test_format_started_at_recent() -> None:
    now = datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc)
    started = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    text = format_started_at(started, now=now)
    assert "мин назад" in text
    assert "12:00 UTC" in text


def test_format_pipeline_status_empty() -> None:
    text = format_pipeline_status(
        {
            "running": [],
            "queue_depths": {"five_verst": 0, "celery": 1, "s95": 0, "parkrun": 0},
        }
    )
    assert "ничего не выполняется" in text
    assert "профили 5v: 1" in text


def test_format_pipeline_status_with_items() -> None:
    started = datetime(2026, 6, 6, 11, 30, tzinfo=timezone.utc)
    text = format_pipeline_status(
        {
            "running": [
                {
                    "label": "5verst: реестр /events/",
                    "started_at": started.isoformat(),
                }
            ],
            "queue_depths": {"five_verst": 2, "celery": 0, "s95": 0, "parkrun": 0},
        }
    )
    assert "5verst: реестр /events/" in text
    assert "5verst: 2" in text
