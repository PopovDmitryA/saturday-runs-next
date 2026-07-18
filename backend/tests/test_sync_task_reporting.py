from __future__ import annotations

from unittest.mock import patch

import pytest

from app.workers.tasks.sync_task_reporting import run_reported_sync


def test_run_reported_sync_skips_s95_batch_during_cooldown() -> None:
    with (
        patch("app.workers.tasks.sync_task_reporting.is_platform_in_cooldown", return_value=True),
        patch("app.workers.tasks.sync_task_reporting.platform_cooldown_until", return_value=1710000000.0),
        patch("app.workers.tasks.sync_task_reporting.record_run") as record,
    ):
        result = run_reported_sync(
            "s95 athletes registry",
            lambda: {"errors": []},
            batch_queue_name="s95",
        )

    assert result["skipped"] is True
    assert result["reason"] == "s95_fetch_cooldown"
    # Пропуск тоже попадает в историю — по ней видно, что задача не пошла.
    record.assert_called_once()
    assert record.call_args.args[1]["reason"] == "s95_fetch_cooldown"


def test_run_reported_sync_records_successful_run() -> None:
    with patch("app.workers.tasks.sync_task_reporting.record_run") as record:
        result = run_reported_sync("runpark latest", lambda: {"events_upserted": 4, "errors": []})

    assert result["events_upserted"] == 4
    record.assert_called_once()
    assert record.call_args.args[0] == "runpark latest"
    assert record.call_args.kwargs["failed"] is False


def test_run_reported_sync_records_failure_and_reraises() -> None:
    def _boom() -> dict[str, object]:
        raise RuntimeError("парсер упал")

    with patch("app.workers.tasks.sync_task_reporting.record_run") as record:
        with pytest.raises(RuntimeError):
            run_reported_sync("5v latest /results/latest/", _boom)

    record.assert_called_once()
    assert record.call_args.kwargs["failed"] is True
    assert record.call_args.args[1]["errors"] == ["парсер упал"]


def test_run_reported_sync_survives_broken_history_write() -> None:
    """Сбой записи истории не должен ронять сам синк."""
    with patch("app.workers.tasks.sync_task_reporting.record_run", side_effect=RuntimeError("db down")):
        result = run_reported_sync("runpark latest", lambda: {"errors": []})

    assert result == {"errors": []}
