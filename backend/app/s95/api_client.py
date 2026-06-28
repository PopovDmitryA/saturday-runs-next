from __future__ import annotations

from dataclasses import dataclass

import httpx

S95_DOMAINS = [
    "https://s95.ru",
    "https://s95.by",
    "https://s95.rs",
]

_TIMEOUT = 30.0
_HEADERS = {"Accept": "application/json", "User-Agent": "saturday-runs/1.0"}


@dataclass(frozen=True)
class S95ApiLocation:
    domain: str
    slug: str
    name: str
    town: str
    place: str
    active: bool
    latitude: float | None = None
    longitude: float | None = None


def _get(url: str) -> list | dict:
    with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def fetch_pages(domain: str) -> list[dict]:
    """GET /pages.json — all locations including those without coordinates."""
    data = _get(f"{domain}/pages.json")
    return data.get("events", [])


def fetch_events(domain: str) -> list[dict]:
    """GET /events.json — locations with coordinates only."""
    data = _get(f"{domain}/events.json")
    if isinstance(data, list):
        return data
    return data.get("events", [])


def fetch_all_locations() -> list[S95ApiLocation]:
    """
    Fetch locations from all three domains, merging pages.json (full list)
    with events.json (coordinates). Returns deduplicated list keyed by (domain, slug).
    """
    result: list[S95ApiLocation] = []

    for domain in S95_DOMAINS:
        # slug → coords from events.json
        coords: dict[str, tuple[float, float]] = {}
        try:
            for item in fetch_events(domain):
                slug = item.get("code_name")
                lat = item.get("latitude")
                lon = item.get("longitude")
                if slug and lat is not None and lon is not None:
                    try:
                        coords[slug] = (float(lat), float(lon))
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass  # coords not critical — proceed with pages only

        try:
            pages = fetch_pages(domain)
        except Exception:
            continue  # skip domain if unreachable

        for item in pages:
            url = item.get("url", "")
            slug = url.rstrip("/").removesuffix(".json").rsplit("/", 1)[-1] if url else None
            if not slug:
                slug = item.get("code_name")
            if not slug:
                continue

            lat, lon = coords.get(slug, (None, None))
            result.append(
                S95ApiLocation(
                    domain=domain,
                    slug=slug,
                    name=item.get("name") or slug,
                    town=item.get("town") or "",
                    place=item.get("place") or "",
                    active=bool(item.get("active", True)),
                    latitude=lat,
                    longitude=lon,
                )
            )

    return result
