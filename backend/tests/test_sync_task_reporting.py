from __future__ import annotations

from unittest.mock import patch

from app.workers.tasks.sync_task_reporting import run_reported_sync


def test_run_reported_sync_skips_s95_batch_during_cooldown() -> None:
    with (
        patch("app.workers.tasks.sync_task_reporting.is_platform_in_cooldown", return_value=True),
        patch("app.workers.tasks.sync_task_reporting.platform_cooldown_until", return_value=1710000000.0),
        patch("app.workers.tasks.sync_task_reporting.notify_sync_started") as started,
        patch("app.workers.tasks.sync_task_reporting.notify_sync_finished") as finished,
    ):
        result = run_reported_sync(
            "s95 athletes registry",
            lambda: {"errors": []},
            batch_queue_name="s95",
        )

    assert result["skipped"] is True
    assert result["reason"] == "s95_fetch_cooldown"
    started.assert_not_called()
    finished.assert_not_called()
