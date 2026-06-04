from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "saturday-runs-stats/1.0 (local dev; contact: info@5verst.ru)"
_last_request_at: float = 0.0


def _fetch_address(latitude: float, longitude: float) -> dict[str, str]:
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
            "zoom": 10,
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
            return value
    return None


def lookup_address(latitude: float, longitude: float) -> dict[str, str | None]:
    """Resolve country, region and city from coordinates via Nominatim (OSM)."""
    address = _fetch_address(latitude, longitude)

    region = None
    for key in ("state", "region", "ISO3166-2-lvl4", "county"):
        value = address.get(key)
        if value:
            region = value
            break

    city = None
    for key in ("city", "town", "village", "municipality", "suburb", "hamlet"):
        value = address.get(key)
        if value:
            city = value
            break

    return {
        "country": address.get("country"),
        "region": region,
        "city": city,
    }
