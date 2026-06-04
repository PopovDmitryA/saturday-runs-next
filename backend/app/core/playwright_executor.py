from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

# Playwright sync API must run in one stable thread (greenlet-bound). asyncio.to_thread
# rotates workers and breaks browser singletons across S95/parkrun fetches.
_playwright_executor: ThreadPoolExecutor | None = None


def _get_playwright_executor() -> ThreadPoolExecutor:
    global _playwright_executor
    if _playwright_executor is None or _playwright_executor._shutdown:
        _playwright_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright-sync")
    return _playwright_executor


async def run_in_playwright_thread(func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    loop = asyncio.get_running_loop()
    bound = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(_get_playwright_executor(), bound)


def shutdown_playwright_executor() -> None:
    global _playwright_executor
    if _playwright_executor is None:
        return
    _playwright_executor.shutdown(wait=False, cancel_futures=True)
    _playwright_executor = None
