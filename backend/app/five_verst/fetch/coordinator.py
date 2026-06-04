from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.core.request_cancel import check_cancelled
from app.five_verst.errors import FiveVerstBanDetected
from app.five_verst.fetch.lock import five_verst_fetch_lock
from app.five_verst.fetch.rate_limit import mark_fetch_completed, wait_for_turn
from app.platform_adapters.five_verst.http import FetchError, _fetch_html_raw
from app.s95.ban import is_ban_or_protection_html

logger = logging.getLogger(__name__)

BAN_COOLDOWN_KEY = "five_verst:fetch:ban_cooldown_until"


def _check_ban_cooldown() -> None:
    redis = get_redis_client()
    raw = redis.get(BAN_COOLDOWN_KEY)
    if raw is None:
        return
    until = float(raw)
    now = time.time()
    if now < until:
        raise FiveVerstBanDetected(f"5verst fetch in cooldown until {until:.0f} (now {now:.0f})")


def _set_ban_cooldown() -> None:
    settings = get_settings()
    redis = get_redis_client()
    until = time.time() + settings.five_verst_ban_cooldown_seconds
    redis.set(BAN_COOLDOWN_KEY, str(until), ex=settings.five_verst_ban_cooldown_seconds + 60)


def fetch_page_html(
    url: str,
    *,
    reason: str = "fetch",
    retries: int = 5,
    retry_delay_sec: float = 3.0,
) -> str:
    """
    Single entry point for all 5verst page loads.
    Serialized via Redis lock + minimum interval between requests.
    """
    check_cancelled()
    _check_ban_cooldown()
    wait_for_turn(reason=reason)
    check_cancelled()

    with five_verst_fetch_lock():
        check_cancelled()
        logger.info("5verst fetch start: %s (%s)", url, reason)
        try:
            html = _fetch_html_raw(url, retries=retries, retry_delay_sec=retry_delay_sec)
        except FetchError as exc:
            if "429" in str(exc) or "Rate limit" in str(exc):
                _set_ban_cooldown()
                raise FiveVerstBanDetected(str(exc)) from exc
            raise
        if is_ban_or_protection_html(html):
            _set_ban_cooldown()
            raise FiveVerstBanDetected(f"Ban/protection page detected for {url}")
        mark_fetch_completed()
        logger.info("5verst fetch done: %s (%s, %d bytes)", url, reason, len(html))
        return html
