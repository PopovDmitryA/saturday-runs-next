from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

S95_USER_QUEUE = "s95_user"

_s95_user_sync_active: ContextVar[bool] = ContextVar("s95_user_sync_active", default=False)


class S95YieldForUserSync(Exception):
    """Batch S95 work should pause so a user profile sync can run first."""


def s95_user_sync_active() -> bool:
    return _s95_user_sync_active.get()


@contextmanager
def s95_user_sync_context() -> Iterator[None]:
    token = _s95_user_sync_active.set(True)
    try:
        yield
    finally:
        _s95_user_sync_active.reset(token)


def s95_user_queue_has_pending() -> bool:
    from app.core.redis_client import get_redis_client

    return int(get_redis_client().llen(S95_USER_QUEUE) or 0) > 0


def check_yield_for_user_sync() -> None:
    """Cooperative preemption: batch fetch stops when a user sync is waiting."""
    if _s95_user_sync_active.get():
        return
    if s95_user_queue_has_pending():
        raise S95YieldForUserSync()
