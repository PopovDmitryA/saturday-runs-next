from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "saturday-runs-stats/1.0 (local dev; contact: info@5verst.ru)"
_last_request_at: float = 0.0

_REGION_SUFFIXES = (" область", " край")


def normalize_region(region: str) -> str:
    """Strip common Russian administrative suffixes for display consistency.

    Nominatim returns "Краснодарский край", "Московская область" etc.,
    while legacy data uses short forms "Краснодарский", "Московская".
    Autonomous okrugs are kept in full as their short form is ambiguous.
    """
    for suffix in _REGION_SUFFIXES:
        if region.endswith(suffix):
            return region[: -len(suffix)]
    return region


def _fetch_address(latitude: float, longitude: float, *, zoom: int = 10) -> dict[str, str]:
    global _last_request_at

    elapsed = time.monotonic() - _last_request_at
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    params = urllib.parse.urlencode(
        {
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "accept-language": "ru",
            "zoom": zoom,
        }
    )
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _last_request_at = time.monotonic()

    address = payload.get("address") or {}
    return {str(key): str(value) for key, value in address.items() if value}


def lookup_region(latitude: float, longitude: float) -> str | None:
    """Resolve region/oblast from coordinates via Nominatim (OSM)."""
    address = _fetch_address(latitude, longitude)
    for key in ("state", "region", "ISO3166-2-lvl4", "county"):
        value = address.get(key)
        if value:
            return normalize_region(value)
    return None


_CITY_KEYS = ("city", "town", "village", "municipality", "suburb", "hamlet")
# Уровень, на котором Nominatim отдаёт малые города («town» в OSM). На zoom 10
# у Усть-Илимска приходит только «городской округ Усть-Илимск», и локация
# осталась без города (репорт Егора 20.10.2026). Глобально zoom не поднимаем:
# тот же запрос нужен lookup_region и подписям «белых пятен» бота.
TOWN_ZOOM = 12


def lookup_address(latitude: float, longitude: float) -> dict[str, str | None]:
    """Resolve country, region and city from coordinates via Nominatim (OSM)."""
    address = _fetch_address(latitude, longitude)
    if not any(address.get(key) for key in _CITY_KEYS):
        # Второй запрос — только когда города нет: это редкость, а лимит
        # Nominatim — запрос в секунду.
        closer = _fetch_address(latitude, longitude, zoom=TOWN_ZOOM)
        address = {**address, **{key: closer[key] for key in _CITY_KEYS if closer.get(key)}}

    region = None
    for key in ("state", "region", "ISO3166-2-lvl4", "county"):
        value = address.get(key)
        if value:
            region = normalize_region(value)
            break

    city = None
    for key in _CITY_KEYS:
        value = address.get(key)
        if value:
            city = value
            break

    return {
        "country": address.get("country"),
        "region": region,
        "city": city,
    }
