from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.request_cancel import check_cancelled
from app.s95.ban import is_ban_or_protection_html
from app.s95.errors import S95BanDetected
from app.s95.fetch.ban_state import (
    BAN_COOLDOWN_KEY,
    ban_cooldown_until,
    clear_ban_cooldown,
    escalate_ban_cooldown,
)
from app.s95.fetch.http import fetch_html_with_httpx, fetch_json_with_httpx
from app.s95.fetch.lock import s95_fetch_lock
from app.s95.fetch.priority import check_yield_for_user_sync
from app.s95.fetch.rate_limit import mark_fetch_completed, wait_for_turn

logger = logging.getLogger(__name__)

__all__ = ["BAN_COOLDOWN_KEY", "fetch_json", "fetch_page_html"]

T = TypeVar("T")


def _check_ban_cooldown() -> None:
    until = ban_cooldown_until()
    if until is None:
        return
    now = time.time()
    if now < until:
        raise S95BanDetected(f"S95 fetch in cooldown until {until:.0f} (now {now:.0f})")


def _fetch(
    url: str,
    *,
    reason: str,
    fetcher: Callable[[str], T],
    is_ban_payload: Callable[[T], bool] | None = None,
) -> T:
    """Единственная дверь ко всем запросам к s95 — и к страницам, и к JSON API.

    Очередь (один запрос за раз на весь кластер), пауза между запросами,
    проверка охлаждения и его эскалация на отказ — всё здесь. До 29.08.2026
    JSON-клиент ходил мимо: без паузы, без охлаждения и без учёта банов, то
    есть примерно две трети наших запросов к s95 никем не сдерживались.
    """
    check_cancelled()
    check_yield_for_user_sync()
    _check_ban_cooldown()
    wait_for_turn(reason=reason)
    check_yield_for_user_sync()
    check_cancelled()
    with s95_fetch_lock():
        check_cancelled()
        logger.info("s95 fetch start: %s (%s)", url, reason)
        try:
            payload = fetcher(url)
        except S95BanDetected:
            until = escalate_ban_cooldown()
            logger.warning("s95 отказал на %s — охлаждение до %.0f", url, until)
            raise
        if is_ban_payload is not None and is_ban_payload(payload):
            until = escalate_ban_cooldown()
            logger.warning("s95 отдал страницу защиты на %s — охлаждение до %.0f", url, until)
            raise S95BanDetected(f"HTTP 403 Forbidden from S95 for {url}")
        mark_fetch_completed()
        # Дверь открыта — лестница эскалации обнуляется, иначе один давний
        # отказ держал бы следующий на ступень выше без причины.
        clear_ban_cooldown()
        logger.info("s95 fetch done: %s (%s)", url, reason)
        return payload


def fetch_page_html(url: str, *, reason: str = "fetch") -> str:
    """HTML-страница s95 (описания площадок, статус локации)."""
    return _fetch(url, reason=reason, fetcher=fetch_html_with_httpx, is_ban_payload=is_ban_or_protection_html)


def fetch_json(url: str, *, reason: str = "fetch") -> Any:
    """JSON API s95 — тем же путём и с тем же лимитом, что и страницы."""
    return _fetch(url, reason=reason, fetcher=fetch_json_with_httpx)
