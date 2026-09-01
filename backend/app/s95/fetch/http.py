from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.s95.errors import S95BanDetected

# Обычный браузерный UA: прод-IP в белом списке s95.ru, но дефолтный
# python-httpx UA всё равно может резаться WAF-ом по сигнатуре.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_JSON_HEADERS = {"Accept": "application/json", "User-Agent": "saturday-runs/1.0"}


def _raise_for_ban(response: httpx.Response, url: str) -> None:
    if response.status_code in {403, 429}:
        raise S95BanDetected(f"HTTP {response.status_code} from S95 for {url}")


def _wrap_transport_error(exc: httpx.HTTPError, url: str) -> Exception:
    """Отказ в соединении — такой же «уходи», как 403, только на уровне фаервола.

    25.08.2026 s95 перешёл с 403 на TCP RST, и `Connection refused` перестал
    считаться баном: охлаждение не вставало, и мы продолжали ходить по
    расписанию в закрытую дверь. Таймаут и прочие сетевые сбои остаются
    обычной ошибкой — по ним лестницу поднимать не за что.
    """
    if isinstance(exc, httpx.ConnectError):
        return S95BanDetected(f"S95 отказал в соединении на {url}: {exc}")
    return RuntimeError(f"S95 HTTP fetch failed for {url}: {exc}")


def fetch_html_with_httpx(url: str) -> str:
    settings = get_settings()
    timeout = settings.s95_http_timeout_seconds
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
    try:
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        raise _wrap_transport_error(exc, url) from exc
    _raise_for_ban(response, url)
    response.raise_for_status()
    return response.text


def fetch_json_with_httpx(url: str) -> Any:
    """JSON API s95 (`/pages.json`, `/events/{slug}.json`, `/activities/{id}.json`)."""
    settings = get_settings()
    timeout = settings.s95_http_timeout_seconds
    try:
        response = httpx.get(url, headers=_JSON_HEADERS, follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        raise _wrap_transport_error(exc, url) from exc
    _raise_for_ban(response, url)
    response.raise_for_status()
    return response.json()
