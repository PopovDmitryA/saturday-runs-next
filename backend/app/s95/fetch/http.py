from __future__ import annotations

import httpx

from app.config import get_settings
from app.s95.errors import S95BanDetected

# Обычный браузерный UA: прод-IP в белом списке s95.ru, но дефолтный
# python-httpx UA всё равно может резаться WAF-ом по сигнатуре.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch_html_with_httpx(url: str) -> str:
    settings = get_settings()
    timeout = settings.s95_http_timeout_seconds
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
    try:
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"S95 HTTP fetch failed for {url}: {exc}") from exc
    if response.status_code in {403, 429}:
        raise S95BanDetected(f"HTTP {response.status_code} from S95 for {url}")
    response.raise_for_status()
    return response.text
