from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from app.config import get_settings
from app.core.request_cancel import check_cancelled

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Playwright


_browser_lock = threading.Lock()
_playwright: Playwright | None = None
_browser: Browser | None = None


def _ensure_browser() -> Browser:
    global _playwright, _browser
    from playwright.sync_api import sync_playwright

    with _browser_lock:
        if _browser is not None:
            return _browser

        from app.parkrun.fetch.browser import shutdown_browser as shutdown_parkrun_browser

        shutdown_parkrun_browser()

        settings = get_settings()
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(
            headless=settings.s95_playwright_headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return _browser


def _page_wait(page, wait_ms: int) -> None:
    remaining = max(wait_ms, 0)
    while remaining > 0:
        check_cancelled()
        step = min(500, remaining)
        page.wait_for_timeout(step)
        remaining -= step


def fetch_html_with_browser(url: str, *, extra_wait_ms: int | None = None, click_map_tab: bool = False) -> str:
    settings = get_settings()
    browser = _ensure_browser()
    page = browser.new_page()
    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=settings.s95_playwright_navigation_timeout_ms,
        )
        wait_ms = extra_wait_ms if extra_wait_ms is not None else settings.s95_playwright_page_wait_ms
        _page_wait(page, wait_ms)
        if click_map_tab:
            for selector in (
                'a[href*="venue"]',
                'button:has-text("Место проведения")',
                'a:has-text("Место проведения")',
            ):
                try:
                    locator = page.locator(selector).first
                    if locator.count() > 0:
                        locator.click(timeout=3000)
                        _page_wait(page, 3000)
                        break
                except Exception:
                    continue
        if extra_wait_ms and extra_wait_ms >= 10000:
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
        return page.content()
    finally:
        page.close()


def shutdown_browser() -> None:
    global _playwright, _browser
    with _browser_lock:
        if _browser is not None:
            _browser.close()
            _browser = None
        if _playwright is not None:
            _playwright.stop()
            _playwright = None
