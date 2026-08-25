"""Пользовательский синк 5 вёрст важнее батча: батч ждёт, а не прерывается.

У батча (сверка истории, ротация) прогон идёт часами. Пользователь, нажавший
«обновить», не должен ждать его конца, поэтому батч замирает между фетчами,
пока идёт пользовательский синк. Прогресс батча при этом не теряется — в
отличие от S95, где batch прерывается исключением и переставляется в очередь.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import fakeredis
import pytest

from app.five_verst.fetch.priority import (
    USER_ACTIVE_KEY,
    five_verst_user_sync_active,
    five_verst_user_sync_context,
    user_queue_has_pending,
    user_sync_in_progress,
    wait_for_user_sync_window,
)


@pytest.fixture
def priority_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.five_verst.fetch.priority.get_redis_client", lambda: redis)
    return redis


def test_context_marks_and_clears_user_sync(priority_redis: fakeredis.FakeRedis) -> None:
    assert five_verst_user_sync_active() is False
    with five_verst_user_sync_context(ttl_seconds=60):
        assert five_verst_user_sync_active() is True
        assert user_sync_in_progress() is True
    assert five_verst_user_sync_active() is False
    assert user_sync_in_progress() is False


def test_active_mark_expires_by_ttl(priority_redis: fakeredis.FakeRedis) -> None:
    """Умерший воркер не должен морозить батч навсегда — отметка живёт по TTL."""
    priority_redis.set(USER_ACTIVE_KEY, "1", ex=60)
    assert user_sync_in_progress() is True
    assert priority_redis.ttl(USER_ACTIVE_KEY) > 0

    priority_redis.delete(USER_ACTIVE_KEY)
    assert user_sync_in_progress() is False


def test_batch_waits_while_user_sync_active(priority_redis: fakeredis.FakeRedis) -> None:
    priority_redis.set(USER_ACTIVE_KEY, "1", ex=60)
    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            # Пользовательский синк закончился — батч должен продолжить.
            priority_redis.delete(USER_ACTIVE_KEY)

    with patch("app.five_verst.fetch.priority.interruptible_sleep", side_effect=fake_sleep):
        waited = wait_for_user_sync_window(max_wait_seconds=30, reason="reconcile")

    assert calls["n"] == 2
    assert waited >= 0


def test_batch_waits_while_user_tasks_queued(priority_redis: fakeredis.FakeRedis) -> None:
    """Задача в очереди пользователей — тоже повод уступить: воркер её вот-вот возьмёт."""
    priority_redis.rpush("five_verst_user", "task")
    assert user_queue_has_pending() is True

    def fake_sleep(_seconds: float) -> None:
        priority_redis.delete("five_verst_user")

    with patch("app.five_verst.fetch.priority.interruptible_sleep", side_effect=fake_sleep):
        wait_for_user_sync_window(max_wait_seconds=30, reason="rotation")

    assert user_queue_has_pending() is False


def test_user_sync_never_waits_for_itself(priority_redis: fakeredis.FakeRedis) -> None:
    """Иначе пользовательский синк заморозил бы сам себя своей же отметкой."""
    with patch("app.five_verst.fetch.priority.interruptible_sleep") as sleep_mock:
        with five_verst_user_sync_context(ttl_seconds=60):
            waited = wait_for_user_sync_window(max_wait_seconds=30, reason="user")
    assert waited == 0.0
    sleep_mock.assert_not_called()


def test_pause_has_ceiling(priority_redis: fakeredis.FakeRedis) -> None:
    """Если воркер пользовательской очереди не поднят, батч не должен встать навсегда."""
    priority_redis.set(USER_ACTIVE_KEY, "1", ex=600)
    started = time.time()

    with patch("app.five_verst.fetch.priority.interruptible_sleep") as sleep_mock:
        # max_wait_seconds=0 — потолок уже исчерпан, ждать нечего.
        waited = wait_for_user_sync_window(max_wait_seconds=0, reason="reconcile")

    assert waited < 1.0
    assert time.time() - started < 1.0
    sleep_mock.assert_not_called()
