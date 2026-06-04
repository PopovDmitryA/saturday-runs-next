#!/usr/bin/env python3
"""Open parkrun in a visible browser so you can pass captcha, then save session for Playwright.

Usage (Python 3.12+, from repo root):

  PARKRUN_PLAYWRIGHT_HEADLESS=false \\
  PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH=data/parkrun_playwright_state.json \\
  docker compose exec api python scripts/parkrun_save_browser_state.py

Or on the host with DATABASE_URL unset — only Playwright is required.

After saving, keep PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH in .env and mount data/ into api container.
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 12):
    print("Python 3.12+ required", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.parkrun.fetch.browser import fetch_html_with_browser, save_browser_session, shutdown_browser


def main() -> int:
    settings = get_settings()
    if settings.parkrun_playwright_headless:
        print(
            "Set PARKRUN_PLAYWRIGHT_HEADLESS=false so the browser window is visible.",
            file=sys.stderr,
        )
        return 2
    if not settings.parkrun_playwright_storage_state_path.strip():
        print(
            "Set PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH=/data/parkrun_playwright_state.json",
            file=sys.stderr,
        )
        return 2

    start_url = f"{settings.parkrun_base_url.rstrip('/')}/parkrunner/"
    print(f"Opening {start_url} — solve captcha if shown, then press Enter here.")
    try:
        fetch_html_with_browser(start_url, extra_wait_ms=3000)
        input("Press Enter after captcha is passed and the page looks normal… ")
        fetch_html_with_browser(start_url, extra_wait_ms=2000)
        if save_browser_session():
            print(f"Saved session to {settings.parkrun_playwright_storage_state_path}")
        else:
            print("Browser context was not started; nothing saved.", file=sys.stderr)
            return 1
    finally:
        shutdown_browser()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
