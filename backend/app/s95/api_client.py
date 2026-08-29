from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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


@dataclass(frozen=True)
class S95ApiActivityRef:
    date: str  # YYYY-MM-DD as returned by the list endpoint
    url: str   # full URL to /activities/{id}.json
    updated_at: str | None = None  # ISO 8601 with offset, e.g. "2026-03-16T16:50:14+03:00"

    def updated_at_dt(self) -> datetime | None:
        if not self.updated_at:
            return None
        try:
            dt = datetime.fromisoformat(self.updated_at)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


def fetch_pages(domain: str) -> list[dict]:
    """GET /pages.json — all locations including those without coordinates."""
    data = _get(f"{domain}/pages.json")
    return data.get("events", [])


def fetch_event_activities(event_url: str) -> list[S95ApiActivityRef]:
    """GET /events/{slug}.json — list of (date, activity_url, updated_at) for a location.

    `event_url` is the full URL from pages.json (already ends with .json).
    Order is NOT chronological — caller must sort if needed.
    """
    data = _get(event_url)
    refs: list[S95ApiActivityRef] = []
    for item in data.get("activities", []) or []:
        url = item.get("url")
        date_str = item.get("date")
        if url and date_str:
            refs.append(S95ApiActivityRef(date=date_str, url=url, updated_at=item.get("updated_at")))
    return refs


def fetch_activity(activity_url: str) -> dict:
    """GET /activities/{id}.json — full protocol payload."""
    data = _get(activity_url)
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected activity payload for {activity_url}")
    return data


def fetch_events(domain: str) -> list[dict]:
    """GET /events.json — locations with coordinates only."""
    data = _get(f"{domain}/events.json")
    if isinstance(data, list):
        return data
    return data.get("events", [])


def fetch_all_locations(*, errors: list[str] | None = None) -> list[S95ApiLocation]:
    """Локации всех трёх доменов: `pages.json` + `events.json`, без дублей.

    Два источника нужны потому, что каждый по отдельности неполон:

    * `pages.json` отдаёт только работающие площадки — координат там нет, зато
      есть разъездные серии вроде «С95 и друзья»;
    * `events.json` отдаёт координаты и — что важнее — единственный признак
      неработающей субботы: `active=false`. Иваново 27.08.2026 отменило старт,
      и в `pages.json` его карточка просто исчезла, а в `events.json` осталась
      с `active=false`.

    Раньше обход шёл только по `pages.json`, поэтому отменённая площадка
    выпадала из синка целиком: её статус у нас так и оставался вчерашним.

    `errors`, если передан, собирает описания несработавших запросов. Пустой
    список после вызова означает «реестр прочитан целиком»; непустой — что
    результат частичный и отсутствие площадки в нём ещё ничего не значит
    (важно для наблюдателя отмен: снимать отметку по неполным данным нельзя).
    """
    result: list[S95ApiLocation] = []

    for domain in S95_DOMAINS:
        events_by_slug: dict[str, dict] = {}
        try:
            for item in fetch_events(domain):
                slug = item.get("code_name")
                if slug:
                    events_by_slug[slug] = item
        except Exception as exc:
            # events.json не критичен — останутся хотя бы pages.json
            if errors is not None:
                errors.append(f"{domain}: events.json: {exc}")

        try:
            pages = fetch_pages(domain)
        except Exception as exc:
            pages = []  # домен недоступен — но events.json мог и ответить
            if errors is not None:
                errors.append(f"{domain}: pages.json: {exc}")

        merged: dict[str, dict] = {}
        order: list[str] = []
        for item in pages:
            url = item.get("url", "")
            slug = url.rstrip("/").removesuffix(".json").rsplit("/", 1)[-1] if url else None
            if not slug:
                slug = item.get("code_name")
            if not slug:
                continue
            if slug not in merged:
                order.append(slug)
            merged[slug] = dict(item)

        for slug, item in events_by_slug.items():
            if slug not in merged:
                order.append(slug)
                merged[slug] = dict(item)
                continue
            # Активность берём по строгому «и»: если хоть один источник говорит,
            # что площадка не бежит, значит не бежит.
            merged[slug]["active"] = bool(merged[slug].get("active", True)) and bool(
                item.get("active", True)
            )

        for slug in order:
            item = merged[slug]
            lat, lon = _coordinates(events_by_slug.get(slug))
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


def _coordinates(item: dict | None) -> tuple[float | None, float | None]:
    if not item:
        return None, None
    lat = item.get("latitude")
    lon = item.get("longitude")
    if lat is None or lon is None:
        return None, None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
