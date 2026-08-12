from __future__ import annotations

import hashlib
from functools import lru_cache

import cloudscraper
import requests

from app.core.request_cancel import check_cancelled, interruptible_sleep

BASE_URL = "https://5verst.ru"


class FetchError(RuntimeError):
    pass


class NotFoundError(LookupError):
    pass


@lru_cache(maxsize=1)
def _scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "linux", "mobile": False},
    )


def source_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _fetch_html_raw(url: str, *, retries: int = 5, retry_delay_sec: float = 3.0) -> str:
    scraper = _scraper()
    last_error: Exception | None = None

    for attempt in range(retries):
        check_cancelled()
        if attempt > 0:
            interruptible_sleep(retry_delay_sec * attempt)
        check_cancelled()
        try:
            response = scraper.get(url, timeout=30)
        except requests.RequestException as exc:
            # Read timeout / обрыв соединения — самая частая ошибка планировщика
            # (5verst.ru регулярно думает дольше 30 секунд). Раньше исключение
            # вылетало мимо цикла, и retries=5 на таймауты не распространялся.
            last_error = exc
            continue
        if response.status_code == 404:
            raise NotFoundError(f"Страница не найдена: {url}")
        if response.status_code == 429:
            last_error = FetchError(f"Rate limit (429): {url}")
            continue
        try:
            response.raise_for_status()
        except Exception as exc:
            last_error = exc
            continue
        html = response.text
        if "404" in html and "Страница не найдена" in html:
            raise NotFoundError(f"Страница не найдена: {url}")
        return html

    if last_error is not None:
        raise FetchError(str(last_error))
    raise FetchError(f"Не удалось загрузить: {url}")


def fetch_html(url: str, *, reason: str = "fetch", retries: int = 5, retry_delay_sec: float = 3.0) -> str:
    from app.five_verst.fetch.coordinator import fetch_page_html

    return fetch_page_html(url, reason=reason, retries=retries, retry_delay_sec=retry_delay_sec)
