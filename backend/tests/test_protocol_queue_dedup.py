from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.protocol_queue_dedup import (
    dedupe_protocol_fetch_queue,
    protocol_fetch_pending_in_broker,
    protocol_fetch_task_id,
)


def test_protocol_fetch_task_id_is_stable() -> None:
    assert (
        protocol_fetch_task_id("five_verst", "bitsa", date(2026, 6, 6))
        == protocol_fetch_task_id("five_verst", "bitsa", date(2026, 6, 6))
    )


def test_protocol_fetch_pending_in_broker_scans_queue() -> None:
    body = json.dumps(
        [
            [],
            {
                "location_slug": "bitsa",
                "event_date_iso": "2026-06-06",
                "location_name": "Битца",
            },
            {},
        ]
    )
    envelope = json.dumps(
        {
            "body": body,
            "headers": {
                "task": "five_verst_sync.fetch_protocol_from_profile",
                "id": "random-uuid",
            },
        }
    )
    with (
        patch("app.services.protocol_queue_dedup.get_celery_task_state", return_value="PENDING"),
        patch("app.services.protocol_queue_dedup.find_task_queue_position", return_value=None),
        patch("app.services.protocol_queue_dedup.get_redis_client") as redis_mock,
    ):
        redis_mock.return_value.lrange.return_value = [envelope]
        assert protocol_fetch_pending_in_broker("five_verst", "bitsa", date(2026, 6, 6))


def test_dedupe_protocol_fetch_queue_dry_run() -> None:
    body = json.dumps(
        [[], {"location_slug": "bitsa", "event_date_iso": "2026-06-06"}, {}]
    )
    envelope = json.dumps(
        {
            "body": body,
            "headers": {"task": "five_verst_sync.fetch_protocol_from_profile", "id": "a"},
        }
    )
    duplicate = json.dumps(
        {
            "body": body,
            "headers": {"task": "five_verst_sync.fetch_protocol_from_profile", "id": "b"},
        }
    )
    other = json.dumps(
        {
            "body": json.dumps([[], {}, {}]),
            "headers": {"task": "five_verst_sync.sync_location_rotation", "id": "c"},
        }
    )
    redis = MagicMock()
    redis.lrange.return_value = [envelope, duplicate, other]
    with patch("app.services.protocol_queue_dedup.get_redis_client", return_value=redis):
        stats = dedupe_protocol_fetch_queue("five_verst", platform_code="five_verst", dry_run=True)
    assert stats == {"total": 3, "kept": 2, "removed": 1}
    redis.delete.assert_not_called()
