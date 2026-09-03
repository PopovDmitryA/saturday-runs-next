"""Город по IP для сообщения бота «откуда вход».

Единственный потребитель — подтверждение входа в Telegram: человеку показываем
браузер, систему и город, чтобы он узнал свой запрос и не подтвердил чужой.
Точность нужна «примерно тот город», поэтому берём бесплатный ip-api.com без
ключа (лимит 45 запросов в минуту с одного адреса — входов у нас на порядки
меньше). Ответ кэшируем в Redis на сутки: повторные входы с того же адреса
внешний сервис не дёргают.

Всё best-effort: таймаут короткий, любая ошибка — «город неизвестен». Вход
из-за этого не ломается, просто строчки с городом в сообщении не будет.
"""

from __future__ import annotations

import ipaddress
import logging

import httpx

from app.config import Settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

CACHE_PREFIX = "ipgeo:"
CACHE_TTL_SECONDS = 24 * 3600
LOOKUP_TIMEOUT_SECONDS = 2.5


def _is_public_ip(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_reserved)


def _fetch(url: str) -> dict | None:
    response = httpx.get(url, timeout=LOOKUP_TIMEOUT_SECONDS)
    if response.status_code != 200:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def _format_place(payload: dict) -> str:
    if payload.get("status") not in (None, "success"):
        return ""
    city = str(payload.get("city") or "").strip()
    country = str(payload.get("country") or "").strip()
    if city and country and country not in {"Россия", "Russia"}:
        return f"{city}, {country}"
    return city or country


def city_for_ip(ip: str, settings: Settings) -> str:
    """«Москва», «Минск, Беларусь» или пустая строка, если не узнали."""
    if not settings.ip_geo_lookup_enabled or not settings.ip_geo_lookup_url or not _is_public_ip(ip):
        return ""

    redis_client = get_redis_client()
    cache_key = f"{CACHE_PREFIX}{ip}"
    try:
        cached = redis_client.get(cache_key)
    except Exception:  # noqa: BLE001 — кэш не обязателен
        cached = None
    if cached is not None:
        return str(cached)

    place = ""
    try:
        payload = _fetch(settings.ip_geo_lookup_url.format(ip=ip))
        if payload is not None:
            place = _format_place(payload)
    except Exception as exc:  # noqa: BLE001 — сеть до внешнего сервиса не наша забота
        logger.info("ip geo lookup failed for %s: %s", ip, exc)
        # Неудачу тоже запоминаем, но ненадолго: не долбить сервис при каждом входе.
        try:
            redis_client.setex(cache_key, 300, "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    try:
        redis_client.setex(cache_key, CACHE_TTL_SECONDS, place)
    except Exception:  # noqa: BLE001
        pass
    return place
