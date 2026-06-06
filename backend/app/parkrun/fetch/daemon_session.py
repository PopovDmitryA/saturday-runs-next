from __future__ import annotations

import logging
import random
import subprocess
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen

from app.config import get_settings
from app.parkrun.errors import ParkrunBanDetected
from app.parkrun.fetch.captcha_state import clear_captcha_pending, set_captcha_pending
from app.parkrun.fetch.captcha_wait import is_parkrun_page_ready, page_url_matches_target
from app.parkrun.fetch.cdp_fetch import fetch_html_with_cdp_page
from app.parkrun.fetch.cdp_session import (
    ParkrunCdpSessionError,
    _format_cdp_connect_error,
    _is_captcha_title,
    _parkrun_host,
)
from app.parkrun.fetch.diagnostics import inspect_html_response
from app.parkrun.fetch.rate_limit import mark_fetch_completed, wait_for_turn
from app.platform_fetch.cooldown import clear_platform_cooldown

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

_active_session: ContextVar[ParkrunDaemonSession | None] = ContextVar(
    "parkrun_daemon_session",
    default=None,
)


def get_active_daemon_session() -> ParkrunDaemonSession | None:
    return _active_session.get()


def activate_daemon_session(session: ParkrunDaemonSession) -> object:
    return _active_session.set(session)


def deactivate_daemon_session(token: object) -> None:
    _active_session.reset(token)


class ParkrunDaemonSession:
    """One browser for the whole queue: Playwright Chromium (default) or optional CDP."""

    def __init__(
        self,
        *,
        use_cdp: bool | None = None,
        cdp_url: str | None = None,
        launch_chrome: bool = True,
        captcha_poll_seconds: float = 5.0,
    ) -> None:
        settings = get_settings()
        self._use_cdp = (
            use_cdp
            if use_cdp is not None
            else bool(settings.parkrun_use_cdp_for_fetch and settings.parkrun_cdp_url.strip())
        )
        self.cdp_url = (cdp_url or settings.parkrun_cdp_url or "").strip()
        self.launch_chrome = launch_chrome
        self.captcha_poll_seconds = captcha_poll_seconds
        self._playwright_mode = False
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._work_page: Page | None = None

    def __enter__(self) -> ParkrunDaemonSession:
        if self._use_cdp:
            return self._enter_cdp()
        return self._enter_playwright()

    def _enter_playwright(self) -> ParkrunDaemonSession:
        from app.parkrun.fetch.browser import _ensure_context

        self._playwright_mode = True
        print(
            "Запуск Chromium (Playwright) — как в legacy-скрипте. "
            "При капче пройдите её в открывшемся окне.",
            flush=True,
        )
        self._context = _ensure_context()
        self._ensure_parkrun_home_tab_playwright()
        return self

    def _enter_cdp(self) -> ParkrunDaemonSession:
        from playwright.sync_api import sync_playwright

        ensure_chrome_cdp(self.cdp_url, launch=self.launch_chrome)
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:
            self.close()
            raise ParkrunCdpSessionError(_format_cdp_connect_error(exc, self.cdp_url), 502) from exc
        self._ensure_parkrun_home_tab_cdp()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._playwright_mode:
            from app.parkrun.fetch.browser import shutdown_browser

            shutdown_browser()
            self._context = None
            self._work_page = None
            return
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._work_page = None

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise ParkrunCdpSessionError("CDP browser not connected", 500)
        return self._browser

    def show_status(self, message: str) -> None:
        line = f"[parkrun queue] {message}"
        print(line, flush=True)
        page = self._work_page
        if page is None:
            return
        try:
            page.evaluate(
                """(msg) => {
                    document.title = msg;
                    let el = document.getElementById('parkrun-queue-status');
                    if (!el) {
                        el = document.createElement('div');
                        el.id = 'parkrun-queue-status';
                        el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;'
                            + 'background:#1a3a5c;color:#fff;padding:12px 16px;font:16px/1.4 system-ui;';
                        document.body.prepend(el);
                    }
                    el.textContent = msg;
                }""",
                line,
            )
        except Exception:
            pass

    def wait_for_captcha_cleared(self, *, pending_url: str | None = None) -> None:
        settings = get_settings()
        parkrun_host = _parkrun_host(settings)
        if pending_url:
            self.show_status(
                f"КАПЧА на {pending_url}: пройдите проверку в окне браузера — скрипт ждёт…"
            )
        else:
            self.show_status("КАПЧА: пройдите проверку в окне браузера — скрипт ждёт…")
        while True:
            if self._playwright_mode:
                cleared = self._playwright_target_ready(pending_url, parkrun_host=parkrun_host)
            else:
                cleared = self._cdp_target_ready(pending_url, parkrun_host=parkrun_host)
            if cleared:
                clear_captcha_pending()
                clear_platform_cooldown("parkrun")
                if self._playwright_mode:
                    from app.parkrun.fetch.browser import save_browser_session

                    save_browser_session()
                self.show_status("Капча пройдена, продолжаем очередь…")
                return
            time.sleep(self.captcha_poll_seconds)

    def _playwright_target_ready(self, pending_url: str | None, *, parkrun_host: str) -> bool:
        from app.parkrun.fetch.browser import _page_content_with_retry

        page = self._ensure_work_page_playwright()
        target = pending_url
        if target:
            if not page_url_matches_target(page.url or "", target):
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(2000)
                except Exception:
                    return False
            try:
                html = _page_content_with_retry(page)
            except Exception:
                return False
            return is_parkrun_page_ready(title=page.title(), html=html, url=target)

        for tab in self._context.pages if self._context else []:
            if parkrun_host not in (tab.url or ""):
                continue
            if _is_captcha_title(tab.title()):
                continue
            self._work_page = tab
            return True
        return False

    def _cdp_target_ready(self, pending_url: str | None, *, parkrun_host: str) -> bool:
        if pending_url:
            for context in self.browser.contexts:
                for page in context.pages:
                    if parkrun_host not in (page.url or ""):
                        continue
                    if not page_url_matches_target(page.url or "", pending_url):
                        continue
                    if _is_captcha_title(page.title()):
                        return False
                    try:
                        html = page.content()
                    except Exception:
                        return False
                    if not is_parkrun_page_ready(
                        title=page.title(), html=html, url=pending_url
                    ):
                        return False
                    self._work_page = page
                    return True
            return False

        for context in self.browser.contexts:
            for page in context.pages:
                if parkrun_host not in (page.url or ""):
                    continue
                if _is_captcha_title(page.title()):
                    continue
                self._work_page = page
                return True
        return False

    def _ensure_work_page_playwright(self):

        if self._context is None:
            raise ParkrunCdpSessionError("Playwright context not started", 500)
        page: Page | None = self._work_page
        if page is None or page.is_closed():
            page = self._context.new_page()
            page.add_init_script(
                'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            )
            self._work_page = page
        return page

    def fetch_page_html(
        self,
        url: str,
        *,
        reason: str = "fetch",
        extra_wait_ms: int | None = None,
    ) -> str:
        del reason
        while True:
            wait_for_turn(reason="daemon")
            try:
                if self._playwright_mode:
                    html = self._fetch_playwright(url, extra_wait_ms=extra_wait_ms)
                else:
                    html, _context, page = fetch_html_with_cdp_page(
                        self.browser,
                        url,
                        extra_wait_ms=extra_wait_ms,
                        preferred_url=url,
                    )
                    self._work_page = page
                mark_fetch_completed()
                return html
            except ParkrunBanDetected:
                logger.warning("parkrun daemon: captcha/protection on %s, waiting for user", url)
                self.wait_for_captcha_cleared(pending_url=url)

    def _fetch_playwright(self, url: str, *, extra_wait_ms: int | None) -> str:
        from app.parkrun.fetch.browser import fetch_html_on_page, save_browser_session

        page = self._ensure_work_page_playwright()
        html = fetch_html_on_page(page, url, extra_wait_ms=extra_wait_ms)
        inspection = inspect_html_response(html, url=url)
        if inspection.is_protection:
            set_captcha_pending(f"playwright:{inspection.summary}")
            raise ParkrunBanDetected(
                f"Ban/protection loading {url} ({inspection.summary}). "
                "Пройдите капчу в окне Chromium."
            )
        save_browser_session()
        return html

    def human_pause_between_jobs(self) -> None:
        settings = get_settings()
        delay = random.uniform(
            settings.parkrun_fetch_min_interval_seconds,
            settings.parkrun_fetch_max_interval_seconds,
        )
        self.show_status(f"Пауза {delay:.0f} с (как между действиями пользователя)…")
        time.sleep(delay)

    def _ensure_parkrun_home_tab_playwright(self) -> None:
        if self._context is None:
            return
        settings = get_settings()
        parkrun_host = _parkrun_host(settings)
        home = f"{settings.parkrun_base_url.rstrip('/')}/"
        for page in self._context.pages:
            if parkrun_host in (page.url or "") and not _is_captcha_title(page.title()):
                self._work_page = page
                self.show_status("Браузер готов (вкладка parkrun)")
                return
        page = self._context.new_page()
        self.show_status("Открываем parkrun.org.uk…")
        page.goto(home, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2000)
        if _is_captcha_title(page.title()):
            self.wait_for_captcha_cleared()
        self._work_page = page

    def _ensure_parkrun_home_tab_cdp(self) -> None:
        settings = get_settings()
        parkrun_host = _parkrun_host(settings)
        home = f"{settings.parkrun_base_url.rstrip('/')}/"
        for context in self.browser.contexts:
            for page in context.pages:
                if parkrun_host in (page.url or "") and not _is_captcha_title(page.title()):
                    self._work_page = page
                    self.show_status("Chrome CDP готов")
                    return
        context = self.browser.contexts[0]
        page = context.new_page()
        self.show_status("Открываем parkrun.org.uk…")
        page.goto(home, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2000)
        if _is_captcha_title(page.title()):
            self.wait_for_captcha_cleared()
        self._work_page = page


# Re-export CDP helpers for admin / optional CDP mode.
def cdp_is_reachable(cdp_url: str, timeout: float = 2.0) -> bool:
    version_url = cdp_url.rstrip("/") + "/json/version"
    try:
        with urlopen(version_url, timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, OSError, ValueError, TimeoutError):
        return False


def manual_chrome_debug_command(cdp_port: int = 9222) -> str:
    profile = Path.home() / ".chrome-parkrun-profile"
    return (
        f'/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome '
        f"--remote-debugging-port={cdp_port} "
        f"--remote-debugging-address=0.0.0.0 "
        f"--remote-allow-origins='*' "
        f'--user-data-dir="{profile}"'
    )


def _chrome_profile_in_use(profile: Path) -> bool:
    return (profile / "SingletonLock").exists() or (profile / "SingletonSocket").exists()


def launch_debug_chrome(cdp_port: int = 9222) -> None:
    import platform

    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).is_file():
        raise ParkrunCdpSessionError(
            "Google Chrome не найден. Установите Chrome или запустите его с --remote-debugging-port=9222.",
            500,
        )
    profile = Path.home() / ".chrome-parkrun-profile"
    profile.mkdir(parents=True, exist_ok=True)
    chrome_args = [
        f"--remote-debugging-port={cdp_port}",
        "--remote-debugging-address=0.0.0.0",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if _chrome_profile_in_use(profile):
        print(
            f"Профиль {profile} уже занят другим Chrome. "
            "Закройте то окно или запустите команду ниже вручную в отдельном терминале:",
            flush=True,
        )
        print(manual_chrome_debug_command(cdp_port), flush=True)
    if platform.system() == "Darwin":
        subprocess.Popen(
            ["open", "-na", "Google Chrome", "--args", *chrome_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        subprocess.Popen(
            [chrome, *chrome_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(f"Запускаем Chrome (профиль {profile}, порт {cdp_port})…", flush=True)


def ensure_chrome_cdp(cdp_url: str, *, launch: bool, wait_seconds: float = 90.0) -> None:
    if cdp_is_reachable(cdp_url):
        print("Chrome CDP уже доступен.", flush=True)
        return
    if not launch:
        raise ParkrunCdpSessionError(
            f"Chrome CDP недоступен: {cdp_url}. Используйте make parkrun без CDP (по умолчанию).",
            400,
        )
    from urllib.parse import urlparse

    parsed = urlparse(cdp_url)
    port = parsed.port or 9222
    launch_debug_chrome(port)
    deadline = time.time() + wait_seconds
    last_progress = 0.0
    while time.time() < deadline:
        if cdp_is_reachable(cdp_url):
            print("Chrome CDP готов.", flush=True)
            return
        now = time.time()
        if now - last_progress >= 5.0:
            remaining = int(deadline - now)
            print(f"Ожидание Chrome CDP на {cdp_url} (~{remaining} с)…", flush=True)
            last_progress = now
        time.sleep(0.5)
    raise ParkrunCdpSessionError(
        f"Chrome не ответил на {cdp_url} за {int(wait_seconds)} с. "
        "Запустите make parkrun без PARKRUN_USE_CDP_FOR_FETCH.",
        502,
    )
