from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any, TypeVar

from starlette.requests import Request

from app.core.playwright_executor import run_in_playwright_thread

_cancel_checker: ContextVar[Callable[[], bool] | None] = ContextVar("request_cancel_checker", default=None)

T = TypeVar("T")


class RequestCancelled(Exception):
    """The HTTP client closed the connection before the work finished."""


def set_cancel_checker(checker: Callable[[], bool] | None) -> Token[Callable[[], bool] | None]:
    return _cancel_checker.set(checker)


def reset_cancel_checker(token: Token[Callable[[], bool] | None]) -> None:
    _cancel_checker.reset(token)


def check_cancelled() -> None:
    checker = _cancel_checker.get()
    if checker is not None and checker():
        raise RequestCancelled()


def interruptible_sleep(seconds: float) -> None:
    if seconds <= 0:
        check_cancelled()
        return
    deadline = time.time() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


async def run_sync_with_disconnect_watch(
    request: Request,
    func: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    disconnected = threading.Event()

    async def watch_disconnect() -> None:
        try:
            while not disconnected.is_set():
                if await request.is_disconnected():
                    disconnected.set()
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(watch_disconnect())
    token = set_cancel_checker(disconnected.is_set)
    try:
        return await run_in_playwright_thread(func, *args, **kwargs)
    finally:
        disconnected.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        reset_cancel_checker(token)
