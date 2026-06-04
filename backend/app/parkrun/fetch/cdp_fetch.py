from __future__ import annotations

import logging

from app.config import get_settings
from app.parkrun.errors import ParkrunBanDetected
from app.parkrun.fetch.captcha_state import set_captcha_pending
from app.parkrun.fetch.cdp_session import (
    ParkrunCdpSessionError,
    _captcha_user_message,
    _fetch_html_in_open_tab,
    _find_parkrun_page,
    _is_captcha_title,
    _normalize_host_path,
    _parkrun_host,
    _persist_cdp_storage_state,
    _same_document_url,
)
from app.parkrun.fetch.diagnostics import inspect_html_response

logger = logging.getLogger(__name__)


def fetch_html_with_cdp_page(
    browser,
    url: str,
    *,
    extra_wait_ms: int | None = None,
    preferred_url: str | None = None,
) -> tuple[str, object, object]:
    """Fetch one URL using an existing CDP browser connection. Returns (html, context, page)."""
    settings = get_settings()
    parkrun_host = _parkrun_host(settings)
    wait_ms = extra_wait_ms if extra_wait_ms is not None else settings.parkrun_playwright_page_wait_ms
    nav_timeout = settings.parkrun_playwright_navigation_timeout_ms
    target = preferred_url or url

    for context in browser.contexts:
        for page in context.pages:
            if parkrun_host in (page.url or "") and _is_captcha_title(page.title()):
                raise ParkrunBanDetected(_captcha_user_message(page.title()))

    context, page = _find_parkrun_page(browser, parkrun_host, preferred_url=target)
    if page is None:
        raise ParkrunCdpSessionError(
            "Нет вкладки parkrun без капчи. Откройте parkrun.org.uk в этом Chrome.",
            400,
        )

    if _is_captcha_title(page.title()):
        raise ParkrunBanDetected(_captcha_user_message(page.title()))

    if _same_document_url(page.url or "", url):
        page.wait_for_timeout(wait_ms)
        html = page.content()
        _validate_fetched_html(html, url=url, via="current_tab")
        _persist_cdp_storage_state(context)
        return html, context, page

    target_host, _ = _normalize_host_path(url)
    if parkrun_host in target_host or target_host.endswith(parkrun_host):
        html = _fetch_html_in_open_tab(page, url, timeout_ms=nav_timeout)
        if html:
            try:
                _validate_fetched_html(html, url=url, via="in_tab_fetch")
                _persist_cdp_storage_state(context)
                return html, context, page
            except ParkrunBanDetected:
                logger.warning("parkrun CDP in-tab fetch protection for %s, trying goto", url)

    logger.info("parkrun CDP goto: %s (from %s)", url, page.url)
    page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
    page.wait_for_timeout(wait_ms)
    if _is_captcha_title(page.title()):
        raise ParkrunBanDetected(_captcha_user_message(page.title()))
    html = page.content()
    _validate_fetched_html(html, url=url, via="goto")
    _persist_cdp_storage_state(context)
    return html, context, page


def _validate_fetched_html(html: str, *, url: str, via: str) -> None:
    inspection = inspect_html_response(html, url=url)
    if inspection.is_protection:
        set_captcha_pending(f"cdp:{via}:{inspection.summary}")
        raise ParkrunBanDetected(
            f"Ban/protection loading {url} via {via} ({inspection.summary}). "
            "Пройдите капчу в окне Chrome."
        )
