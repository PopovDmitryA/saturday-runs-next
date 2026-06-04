from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from app.config import get_settings
from app.parkrun.errors import ParkrunBanDetected
from app.parkrun.fetch.browser import shutdown_browser
from app.parkrun.fetch.captcha_state import clear_captcha_pending, set_captcha_pending
from app.parkrun.fetch.diagnostics import inspect_html_response
from app.platform_fetch.cooldown import clear_platform_cooldown

_PARKRUNNER_ID_RE = re.compile(r"/parkrunner/(\d+)", re.IGNORECASE)

logger = logging.getLogger(__name__)


class ParkrunCdpSessionError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _parkrun_host(settings) -> str:
    return urlparse(settings.parkrun_base_url).hostname or "www.parkrun.org.uk"


def _format_cdp_connect_error(exc: Exception, cdp_url: str) -> str:
    raw = str(exc)
    lowered = raw.lower()
    if "500" in raw and "json/version" in lowered:
        return (
            "Chrome на порту 9222 не принимает CDP из Docker (HTTP 500 на /json/version). "
            "На Mac запустите в терминале: make parkrun-save-cdp-host "
            "(Chrome с --remote-allow-origins='*' и --remote-debugging-address=0.0.0.0). "
            f"URL: {cdp_url}"
        )
    if "connection refused" in lowered or "econnrefused" in lowered:
        return (
            "На Mac не запущен Chrome с --remote-debugging-port=9222. "
            "Запустите отладочный Chrome командой из админки и оставьте окно открытым. "
            f"URL: {cdp_url}"
        )
    if "does not look like a devtools server" in lowered:
        return (
            "Порт 9222 занят не Chrome DevTools. Закройте лишние процессы на 9222, "
            "запустите Chrome с --remote-debugging-port=9222 и --remote-allow-origins=*. "
            f"URL: {cdp_url}"
        )
    return f"Не удалось подключиться к Chrome: {raw}"


def _is_captcha_title(title: str) -> bool:
    lowered = title.strip().lower()
    return "human verification" in lowered or lowered == "attention required"


def _normalize_host_path(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "/").rstrip("/") or "/"
    return host, path


def _same_document_url(page_url: str, target_url: str) -> bool:
    return _normalize_host_path(page_url) == _normalize_host_path(target_url)


def _athlete_id_from_url(url: str) -> str | None:
    match = _PARKRUNNER_ID_RE.search(url)
    return match.group(1) if match else None


def _captcha_user_message(title: str) -> str:
    return (
        f"В отладочном Chrome открыта капча ({title!r}). Пройдите её в этом же окне Chrome "
        "(не в обычном браузере), дождитесь страницы parkrunner, затем повторите синхронизацию. "
        "Лучше держать вкладку профиля parkrun открытой — скрипт подгрузит /all без полной перезагрузки."
    )


def _find_parkrun_page(browser, parkrun_host: str, *, preferred_url: str | None = None):
    from playwright.sync_api import Browser

    preferred_athlete = _athlete_id_from_url(preferred_url) if preferred_url else None
    typed: Browser = browser
    fallback: tuple[object, object] | None = None
    for context in typed.contexts:
        for page in context.pages:
            page_url = page.url or ""
            if parkrun_host not in page_url:
                continue
            title = page.title()
            if _is_captcha_title(title):
                continue
            if preferred_athlete and f"/parkrunner/{preferred_athlete}" in page_url:
                return context, page
            if fallback is None:
                fallback = (context, page)
    return fallback if fallback else (None, None)


def _persist_cdp_storage_state(context) -> None:
    settings = get_settings()
    storage_raw = settings.parkrun_playwright_storage_state_path.strip()
    if not storage_raw:
        return
    path = Path(storage_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    logger.info("parkrun CDP: refreshed storage state at %s", path)


def _fetch_html_in_open_tab(page, url: str, *, timeout_ms: int) -> str | None:
    """Same-origin fetch in the user's tab — avoids page.goto that often re-triggers captcha."""
    try:
        return page.evaluate(
            """async (targetUrl) => {
                const res = await fetch(targetUrl, {
                    credentials: "include",
                    redirect: "follow",
                });
                return await res.text();
            }""",
            url,
            timeout=timeout_ms,
        )
    except Exception as exc:
        logger.warning("parkrun CDP in-tab fetch failed for %s: %s", url, exc)
        return None


def fetch_html_via_chrome_cdp(
    cdp_url: str,
    url: str,
    *,
    extra_wait_ms: int | None = None,
) -> str:
    """Load URL via the user's Chrome (CDP). Prefer in-tab fetch over goto to avoid re-captcha."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise ParkrunCdpSessionError(
                    "В Chrome нет вкладок. Запустите отладочный Chrome и откройте parkrun.org.uk.",
                    400,
                )
            html, _context, _page = fetch_html_with_cdp_page(
                browser,
                url,
                extra_wait_ms=extra_wait_ms,
                preferred_url=url,
            )
            logger.info("parkrun CDP fetch ok: %s bytes=%d", url, len(html))
            return html
    except (ParkrunCdpSessionError, ParkrunBanDetected):
        raise
    except Exception as exc:
        logger.exception("parkrun CDP fetch failed for %s", url)
        raise ParkrunCdpSessionError(_format_cdp_connect_error(exc, cdp_url), 502) from exc


def save_session_from_chrome_cdp(cdp_url: str) -> dict[str, object]:
    settings = get_settings()
    storage_raw = settings.parkrun_playwright_storage_state_path.strip()
    if not storage_raw:
        raise ParkrunCdpSessionError(
            "Задайте PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH=/data/parkrun_playwright_state.json в .env",
            503,
        )
    storage_path = Path(storage_raw)
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    parkrun_home = f"{settings.parkrun_base_url.rstrip('/')}/parkrunner/"
    parkrun_host = _parkrun_host(settings)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise ParkrunCdpSessionError(
                    "В Chrome нет открытых вкладок. Запустите Chrome с отладкой и откройте parkrun.org.uk.",
                    400,
                )
            context, page = _find_parkrun_page(browser, parkrun_host, preferred_url=parkrun_home)
            navigated = False
            if page is None:
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                logger.info("parkrun CDP: no parkrun tab, navigating %s", parkrun_home)
                page.goto(parkrun_home, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3000)
                navigated = True
            else:
                logger.info(
                    "parkrun CDP: using open tab %s (no navigation — avoids re-triggering captcha)",
                    page.url,
                )
                page.wait_for_timeout(500)
            title = page.title()
            if _is_captcha_title(title):
                raise ParkrunCdpSessionError(
                    "В Chrome всё ещё страница капчи. Пройдите проверку вручную на parkrun.org.uk "
                    "(без «Сохранить сессию»), дождитесь нормальной страницы, затем нажмите кнопку снова. "
                    f"Заголовок вкладки: {title!r}",
                    400,
                )
            context.storage_state(path=str(storage_path))
    except ParkrunCdpSessionError:
        raise
    except Exception as exc:
        logger.exception("parkrun CDP session save failed")
        raise ParkrunCdpSessionError(_format_cdp_connect_error(exc, cdp_url), 502) from exc

    shutdown_browser()
    clear_captcha_pending()
    clear_platform_cooldown("parkrun")

    nav_hint = " (вкладка parkrun уже была открыта, без повторного захода)" if not navigated else ""
    return {
        "storage_path": str(storage_path),
        "page_title": title,
        "cdp_url": cdp_url,
        "message": f"Сессия parkrun сохранена{nav_hint}. Можно обрабатывать очередь и предпросмотр в ЛК.",
    }
