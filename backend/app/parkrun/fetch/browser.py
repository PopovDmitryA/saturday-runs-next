from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Playwright

logger = logging.getLogger(__name__)

PARKRUN_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_browser_lock = threading.Lock()
_playwright: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None


def _storage_state_path() -> Path | None:
    raw = get_settings().parkrun_playwright_storage_state_path.strip()
    if not raw:
        return None
    return Path(raw)


def _persist_storage_state(context: BrowserContext) -> None:
    path = _storage_state_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    logger.info("parkrun playwright: saved storage state to %s", path)


def _ensure_context() -> BrowserContext:
    global _playwright, _browser, _context
    from playwright.sync_api import sync_playwright

    with _browser_lock:
        if _context is not None:
            return _context

        try:
            from app.s95.fetch.browser import shutdown_browser as shutdown_s95_browser

            shutdown_s95_browser()
        except Exception:
            logger.warning("parkrun playwright: could not shutdown S95 browser (ignored)", exc_info=True)

        settings = get_settings()
        storage_path = _storage_state_path()
        has_storage = storage_path is not None and storage_path.is_file()
        logger.info(
            "parkrun playwright: starting chromium headless=%s storage_state=%s",
            settings.parkrun_playwright_headless,
            storage_path if has_storage else "none",
        )
        try:
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(
                headless=settings.parkrun_playwright_headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context_kwargs: dict = {
                "user_agent": PARKRUN_USER_AGENT,
                "locale": "en-GB",
                "viewport": {"width": 1280, "height": 800},
            }
            if has_storage:
                context_kwargs["storage_state"] = str(storage_path)
            _context = _browser.new_context(**context_kwargs)
            logger.info("parkrun playwright: browser context ready")
            return _context
        except Exception:
            logger.exception("parkrun playwright: failed to start browser")
            shutdown_browser()
            raise


def _page_content_with_retry(page, *, attempts: int = 8, delay_ms: int = 400) -> str:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return page.content()
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "navigating" not in msg and "changing the content" not in msg:
                raise
            if attempt + 1 >= attempts:
                break
            page.wait_for_timeout(delay_ms)
    assert last_exc is not None
    raise last_exc


def fetch_html_on_page(page, url: str, *, extra_wait_ms: int | None = None):
    """Load url in an existing tab (daemon queue keeps one tab for captcha UX)."""
    settings = get_settings()
    response = page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=settings.parkrun_playwright_navigation_timeout_ms,
    )
    wait_ms = extra_wait_ms if extra_wait_ms is not None else settings.parkrun_playwright_page_wait_ms
    page.wait_for_timeout(wait_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(wait_ms, 15000))
    except Exception:
        pass
    html = _page_content_with_retry(page)
    status = response.status if response is not None else None
    logger.info(
        "parkrun playwright: loaded url=%s final_url=%s status=%s title=%r bytes=%d",
        url,
        page.url,
        status,
        page.title(),
        len(html),
    )
    return html


def fetch_html_with_browser(url: str, *, extra_wait_ms: int | None = None) -> str:
    settings = get_settings()
    context = _ensure_context()
    page = context.new_page()
    try:
        page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        return fetch_html_on_page(page, url, extra_wait_ms=extra_wait_ms)
    except Exception:
        logger.exception("parkrun playwright: navigation failed for %s", url)
        raise
    finally:
        page.close()


def save_browser_session() -> bool:
    """Persist cookies/local storage after a successful manual or automated login."""
    with _browser_lock:
        if _context is None:
            return False
        _persist_storage_state(_context)
        return True


def shutdown_browser() -> None:
    global _playwright, _browser, _context
    with _browser_lock:
        if _context is not None:
            _context.close()
            _context = None
        if _browser is not None:
            _browser.close()
            _browser = None
        if _playwright is not None:
            _playwright.stop()
            _playwright = None
