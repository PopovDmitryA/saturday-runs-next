from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.s95.fetch import S95_STOP_EXCEPTIONS, fetch_json

S95_DOMAINS = [
    "https://s95.ru",
    "https://s95.by",
    "https://s95.rs",
]


class S95RegistryUnavailable(RuntimeError):
    """Ни один домен s95 не отдал реестр локаций.

    Пустой список раньше возвращался молча, и синк отчитывался «ok» с нулями:
    24-25.08.2026 s95.ru закрылся от нашего IP (403, затем connection refused),
    а «Автообновление» пять суток показывало зелёные прогоны — протоколы за
    22 августа так и не доехали. Обрыв связи обязан валить прогон.
    """


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


def _get(url: str, *, reason: str = "api") -> list | dict:
    """JSON API s95 — через общий координатор: очередь, пауза, охлаждение.

    Раньше ходили напрямую своим httpx-клиентом, мимо всех ограничителей.
    """
    return fetch_json(url, reason=reason)


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
    data = _get(f"{domain}/pages.json", reason="s95_pages")
    return data.get("events", [])


def fetch_event_activities(event_url: str) -> list[S95ApiActivityRef]:
    """GET /events/{slug}.json — list of (date, activity_url, updated_at) for a location.

    `event_url` is the full URL from pages.json (already ends with .json).
    Order is NOT chronological — caller must sort if needed.
    """
    data = _get(event_url, reason="s95_event_activities")
    refs: list[S95ApiActivityRef] = []
    for item in data.get("activities", []) or []:
        url = item.get("url")
        date_str = item.get("date")
        if url and date_str:
            refs.append(S95ApiActivityRef(date=date_str, url=url, updated_at=item.get("updated_at")))
    return refs


def fetch_activity(activity_url: str) -> dict:
    """GET /activities/{id}.json — full protocol payload."""
    data = _get(activity_url, reason="s95_activity")
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected activity payload for {activity_url}")
    return data


def fetch_events(domain: str) -> list[dict]:
    """GET /events.json — locations with coordinates only."""
    data = _get(f"{domain}/events.json", reason="s95_events")
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
    результат ЧАСТИЧНЫЙ и отсутствие площадки в нём ещё ничего не значит
    (важно наблюдателю отмен: снимать отметку по неполным данным нельзя).
    Полный провал всех доменов по-прежнему валит вызов S95RegistryUnavailable.
    """
    result: list[S95ApiLocation] = []
    failures: list[str] = []

    for domain in S95_DOMAINS:
        events_by_slug: dict[str, dict] = {}
        try:
            for item in fetch_events(domain):
                slug = item.get("code_name")
                if slug:
                    events_by_slug[slug] = item
        except S95_STOP_EXCEPTIONS:
            # Не «домен не ответил», а «нас не пускают» либо «ждёт пользователь»:
            # такое решает вызывающий проход, а не сборщик реестра.
            raise
        except Exception as exc:
            failures.append(f"{domain}/events.json: {exc}")
            # events.json не критичен — останутся хотя бы pages.json

        try:
            pages = fetch_pages(domain)
        except S95_STOP_EXCEPTIONS:
            raise
        except Exception as exc:
            failures.append(f"{domain}/pages.json: {exc}")
            pages = []  # домен недоступен — но events.json мог и ответить

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

    if errors is not None:
        errors.extend(failures)

    if not result:
        raise S95RegistryUnavailable(
            "реестр локаций s95 пуст: " + ("; ".join(failures) or "все домены ответили пустым списком")
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
