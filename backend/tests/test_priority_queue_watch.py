"""Сторож приоритетных очередей: тишина, пока очередь разбирают.

Проверяем ровно то, чего не хватило 17–19.09.2026: стоящая очередь сама
приходит в Telegram, а не ждёт, пока её увидят глазами на сайте.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.core import redis_client
from app.services import priority_queue_watch as watch

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "_test_redis_override", client)
    yield client
    monkeypatch.setattr(redis_client, "_test_redis_override", None)


def _enqueue(redis: fakeredis.FakeRedis, queue: str, message_id: str, task: str = "five_verst_sync.sync_latest_results") -> None:
    redis.lpush(queue, json.dumps({"headers": {"id": message_id, "task": task}}))


def test_empty_queue_says_nothing(redis: fakeredis.FakeRedis) -> None:
    assert watch.check_priority_queues(NOW) == []


def test_fresh_message_is_not_a_stall(redis: fakeredis.FakeRedis) -> None:
    """Первый заход только запоминает, кто стоит на хвосте."""

    _enqueue(redis, "five_verst_fresh", "task-1")
    assert watch.check_priority_queues(NOW) == []
    assert watch.check_priority_queues(NOW + timedelta(minutes=10)) == []


def test_same_message_after_half_an_hour_is_a_stall(redis: fakeredis.FakeRedis) -> None:
    _enqueue(redis, "five_verst_fresh", "task-1")
    watch.check_priority_queues(NOW)

    stalled = watch.check_priority_queues(NOW + timedelta(minutes=31))

    assert [item.queue for item in stalled] == ["five_verst_fresh"]
    assert stalled[0].task == "five_verst_sync.sync_latest_results"
    assert "five_verst_fresh" in watch.format_stall_alert(stalled)


def test_alert_fires_once_per_incident(redis: fakeredis.FakeRedis) -> None:
    """Иначе каждые 15 минут прилетало бы одно и то же сообщение."""

    _enqueue(redis, "five_verst_fresh", "task-1")
    watch.check_priority_queues(NOW)
    watch.check_priority_queues(NOW + timedelta(minutes=31))

    assert watch.check_priority_queues(NOW + timedelta(minutes=46)) == []


def test_moving_queue_resets_the_clock(redis: fakeredis.FakeRedis) -> None:
    """Очередь разбирают — значит, ждать некому, сколько бы задач в ней ни было."""

    _enqueue(redis, "five_verst_fresh", "task-1")
    watch.check_priority_queues(NOW)
    redis.delete("five_verst_fresh")
    _enqueue(redis, "five_verst_fresh", "task-2")

    assert watch.check_priority_queues(NOW + timedelta(minutes=31)) == []
    assert watch.check_priority_queues(NOW + timedelta(minutes=45)) == []


def test_background_queues_are_not_watched() -> None:
    """В фоне ожидание в часах — норма, сторожу там делать нечего."""

    assert "five_verst" not in watch.PRIORITY_QUEUES
    assert set(watch.PRIORITY_QUEUES) == {"five_verst_fresh", "five_verst_user", "s95_user"}
