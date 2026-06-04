from __future__ import annotations

import threading
import time

import pytest

from app.core.request_cancel import (
    RequestCancelled,
    check_cancelled,
    interruptible_sleep,
    reset_cancel_checker,
    set_cancel_checker,
)
from app.s95.fetch.rate_limit import wait_for_turn


def test_check_cancelled_raises_when_flag_set() -> None:
    cancelled = threading.Event()
    token = set_cancel_checker(cancelled.is_set)
    try:
        check_cancelled()
        cancelled.set()
        with pytest.raises(RequestCancelled):
            check_cancelled()
    finally:
        reset_cancel_checker(token)


def test_interruptible_sleep_respects_cancel() -> None:
    cancelled = threading.Event()
    token = set_cancel_checker(cancelled.is_set)

    def trigger_cancel() -> None:
        time.sleep(0.05)
        cancelled.set()

    starter = threading.Thread(target=trigger_cancel, daemon=True)
    starter.start()
    try:
        with pytest.raises(RequestCancelled):
            interruptible_sleep(2.0)
    finally:
        reset_cancel_checker(token)
        starter.join(timeout=1.0)


def test_wait_for_turn_exits_when_client_disconnects(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.s95.fetch.rate_limit.get_settings",
        lambda: type(
            "Settings",
            (),
            {"s95_fetch_min_interval_seconds": 30.0, "s95_fetch_max_interval_seconds": 30.0},
        )(),
    )
    monkeypatch.setattr(
        "app.s95.fetch.rate_limit.get_redis_client",
        lambda: type("Redis", (), {"get": lambda self, key: str(time.time())})(),
    )

    cancelled = threading.Event()
    token = set_cancel_checker(cancelled.is_set)

    def trigger_cancel() -> None:
        time.sleep(0.05)
        cancelled.set()

    starter = threading.Thread(target=trigger_cancel, daemon=True)
    starter.start()
    try:
        with pytest.raises(RequestCancelled):
            wait_for_turn(reason="test")
    finally:
        reset_cancel_checker(token)
        starter.join(timeout=1.0)
