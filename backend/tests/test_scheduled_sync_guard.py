from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.services.scheduled_sync_guard import (
    _hour_bucket,
    release_hourly_sync_slot,
    try_claim_hourly_sync_slot,
)


def test_hour_bucket_moscow() -> None:
    ts = datetime(2026, 6, 7, 19, 45, tzinfo=ZoneInfo("Europe/Moscow"))
    assert _hour_bucket(ts) == "2026-06-07T19"


@patch("app.services.scheduled_sync_guard.get_redis_client")
def test_try_claim_hourly_sync_slot_dedupes(redis_factory: MagicMock) -> None:
    redis = MagicMock()
    redis.set.side_effect = [True, False]
    redis_factory.return_value = redis

    assert try_claim_hourly_sync_slot("s95:latest") is True
    assert try_claim_hourly_sync_slot("s95:latest") is False
    assert redis.set.call_count == 2


@patch("app.services.scheduled_sync_guard.get_redis_client")
def test_force_bypasses_slot(redis_factory: MagicMock) -> None:
    redis = MagicMock()
    redis_factory.return_value = redis

    assert try_claim_hourly_sync_slot("s95:latest", force=True) is True
    redis.set.assert_not_called()


@patch("app.services.scheduled_sync_guard.get_redis_client")
def test_release_hourly_sync_slot(redis_factory: MagicMock) -> None:
    redis = MagicMock()
    redis_factory.return_value = redis
    ts = datetime(2026, 6, 7, 19, 45, tzinfo=ZoneInfo("Europe/Moscow"))

    release_hourly_sync_slot("s95:latest", now=ts)

    redis.delete.assert_called_once_with("sync-hour-slot:s95:latest:2026-06-07T19")
