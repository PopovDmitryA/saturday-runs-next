from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.core.request_cancel import check_cancelled
from app.s95.ban import is_ban_or_protection_html
from app.s95.errors import S95BanDetected
from app.s95.fetch.http import fetch_html_with_httpx
from app.s95.fetch.lock import s95_fetch_lock
from app.s95.fetch.priority import check_yield_for_user_sync
from app.s95.fetch.rate_limit import mark_fetch_completed, wait_for_turn

logger = logging.getLogger(__name__)

BAN_COOLDOWN_KEY = "s95:fetch:ban_cooldown_until"


def _check_ban_cooldown() -> None:
    redis = get_redis_client()
    raw = redis.get(BAN_COOLDOWN_KEY)
    if raw is None:
        return
    until = float(raw)
    now = time.time()
    if now < until:
        raise S95BanDetected(f"S95 fetch in cooldown until {until:.0f} (now {now:.0f})")


def _set_ban_cooldown() -> None:
    settings = get_settings()
    redis = get_redis_client()
    until = time.time() + settings.s95_ban_cooldown_seconds
    redis.set(BAN_COOLDOWN_KEY, str(until), ex=settings.s95_ban_cooldown_seconds + 60)


def fetch_page_html(
    url: str,
    *,
    reason: str = "fetch",
) -> str:
    """
    Single entry point for all S95 page loads.
    Serialized via Redis lock + minimum interval between requests.
    """
    from app.debug_agent_log import agent_log

    started = time.time()
    agent_log(
        location="coordinator.py:fetch_page_html:entry",
        message="s95 fetch entry",
        data={"reason": reason, "url_host": url.split("/")[2] if "://" in url else "unknown"},
        hypothesis_id="A",
    )
    try:
        check_cancelled()
        check_yield_for_user_sync()
        _check_ban_cooldown()
        wait_for_turn(reason=reason)
        check_yield_for_user_sync()
        check_cancelled()
        agent_log(
            location="coordinator.py:fetch_page_html:after_wait",
            message="passed ban check and rate wait",
            data={"reason": reason, "elapsed_ms": int((time.time() - started) * 1000)},
            hypothesis_id="B",
        )
        with s95_fetch_lock():
            check_cancelled()
            agent_log(
                location="coordinator.py:fetch_page_html:lock_acquired",
                message="s95 fetch lock acquired",
                data={"reason": reason, "elapsed_ms": int((time.time() - started) * 1000)},
                hypothesis_id="B",
            )
            logger.info("s95 fetch start: %s (%s)", url, reason)
            try:
                html = fetch_html_with_httpx(url)
            except S95BanDetected:
                _set_ban_cooldown()
                raise
            if is_ban_or_protection_html(html):
                _set_ban_cooldown()
                agent_log(
                    location="coordinator.py:fetch_page_html:ban",
                    message="ban page detected",
                    data={"reason": reason, "html_len": len(html)},
                    hypothesis_id="F",
                )
                raise S95BanDetected(f"HTTP 403 Forbidden from S95 for {url}")
            mark_fetch_completed()
            logger.info("s95 fetch done: %s (%s, %d bytes)", url, reason, len(html))
            agent_log(
                location="coordinator.py:fetch_page_html:success",
                message="s95 fetch success",
                data={"reason": reason, "html_len": len(html), "elapsed_ms": int((time.time() - started) * 1000)},
                hypothesis_id="A",
            )
            return html
    except Exception as exc:
        agent_log(
            location="coordinator.py:fetch_page_html:error",
            message="s95 fetch failed",
            data={
                "reason": reason,
                "error_type": type(exc).__name__,
                "error_msg": str(exc)[:300],
                "elapsed_ms": int((time.time() - started) * 1000),
            },
            hypothesis_id="D",
        )
        raise
