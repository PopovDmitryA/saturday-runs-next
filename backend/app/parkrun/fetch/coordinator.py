from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.core.redis_client import get_redis_client
from app.core.request_cancel import check_cancelled
from app.parkrun.errors import ParkrunBanDetected
from app.parkrun.fetch.browser import fetch_html_with_browser, save_browser_session
from app.parkrun.fetch.captcha_state import (
    captcha_pending_message,
    is_captcha_pending,
    set_captcha_pending,
)
from app.parkrun.fetch.diagnostics import inspect_html_response
from app.parkrun.fetch.lock import parkrun_fetch_lock
from app.parkrun.fetch.rate_limit import mark_fetch_completed, wait_for_turn

logger = logging.getLogger(__name__)

BAN_COOLDOWN_KEY = "parkrun:fetch:ban_cooldown_until"


def _check_ban_cooldown() -> None:
    redis = get_redis_client()
    raw = redis.get(BAN_COOLDOWN_KEY)
    if raw is None:
        return
    until = float(raw)
    now = time.time()
    if now < until:
        raise ParkrunBanDetected(f"parkrun fetch in cooldown until {until:.0f}")


def _set_ban_cooldown() -> None:
    settings = get_settings()
    redis = get_redis_client()
    until = time.time() + settings.parkrun_ban_cooldown_seconds
    redis.set(BAN_COOLDOWN_KEY, str(until), ex=settings.parkrun_ban_cooldown_seconds + 60)


def _check_captcha_pending() -> None:
    if not is_captcha_pending():
        return
    detail = captcha_pending_message() or "captcha required"
    raise ParkrunBanDetected(
        f"parkrun fetch paused until captcha cleared ({detail}). "
        "On Mac run: make parkrun (pass captcha in the Chromium window)."
    )


def _log_protection_failure(inspection, *, reason: str) -> None:
    logger.warning(
        "parkrun fetch blocked (%s): %s — %s. "
        "If you pass captcha in a normal browser, export session: "
        "PARKRUN_PLAYWRIGHT_HEADLESS=false, then set PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH.",
        reason,
        inspection.url,
        inspection.summary,
    )


def fetch_page_html(url: str, *, reason: str = "fetch", extra_wait_ms: int | None = None) -> str:
    from app.parkrun.fetch.daemon_session import get_active_daemon_session

    daemon = get_active_daemon_session()
    if daemon is not None:
        check_cancelled()
        with parkrun_fetch_lock():
            check_cancelled()
            logger.info("parkrun daemon fetch: %s (reason=%s)", url, reason)
            return daemon.fetch_page_html(url, reason=reason, extra_wait_ms=extra_wait_ms)

    check_cancelled()
    _check_captcha_pending()
    _check_ban_cooldown()
    wait_for_turn(reason=reason)
    check_cancelled()

    with parkrun_fetch_lock():
        check_cancelled()
        logger.info("parkrun fetch start: %s (reason=%s)", url, reason)
        settings = get_settings()
        html: str
        if settings.parkrun_use_cdp_for_fetch and settings.parkrun_cdp_url.strip():
            from app.parkrun.fetch.cdp_session import fetch_html_via_chrome_cdp

            logger.info("parkrun fetch: using Chrome CDP at %s", settings.parkrun_cdp_url)
            html = fetch_html_via_chrome_cdp(
                settings.parkrun_cdp_url.strip(),
                url,
                extra_wait_ms=extra_wait_ms,
            )
        else:
            html = fetch_html_with_browser(url, extra_wait_ms=extra_wait_ms)
        inspection = inspect_html_response(html, url=url)
        if inspection.is_protection:
            _log_protection_failure(inspection, reason=reason)
            set_captcha_pending(f"{reason}: {inspection.summary}")
            _set_ban_cooldown()
            detail = inspection.summary
            if inspection.matched_markers:
                detail = f"{detail}; likely captcha/WAF ({', '.join(inspection.matched_markers)})"
            elif inspection.is_too_short:
                detail = f"{detail}; page too short — browser may not have rendered (check playwright)"
            raise ParkrunBanDetected(f"Ban/protection page detected for {url} ({detail})")
        mark_fetch_completed()
        if save_browser_session():
            logger.info("parkrun fetch: persisted browser storage state after success")
        logger.info(
            "parkrun fetch ok: %s (reason=%s, %s)",
            url,
            reason,
            inspection.summary,
        )
        return html
