"""Сборка списка локаций s95 из двух неполных источников.

27.08.2026 Иваново отменило старт — и его карточка исчезла из `pages.json`,
оставшись в `events.json` с `active=false`. Обход только по `pages.json`
пропускал такую площадку целиком: статус у нас оставался вчерашним.
"""

from __future__ import annotations

from unittest.mock import patch

from app.s95.api_client import fetch_all_locations

PAGES = [
    {"url": "https://s95.ru/events/lensk.json", "name": "Ленск", "town": "Ленск", "active": True},
    {"url": "https://s95.ru/events/s.json", "name": "С95 и друзья", "town": "", "active": True},
]

EVENTS = [
    {"code_name": "lensk", "name": "Ленск", "town": "Ленск", "active": True,
     "latitude": "60.716579", "longitude": "114.909692"},
    {"code_name": "ivanovo", "name": "Иваново", "town": "Иваново", "active": False,
     "latitude": "57.00297", "longitude": "40.976711"},
]


def _fetch_all(pages: list[dict], events: list[dict]) -> dict[str, object]:
    with (
        patch("app.s95.api_client.S95_DOMAINS", ["https://s95.ru"]),
        patch("app.s95.api_client.fetch_pages", return_value=pages),
        patch("app.s95.api_client.fetch_events", return_value=events),
    ):
        return {row.slug: row for row in fetch_all_locations()}


def test_inactive_location_survives_missing_pages_entry() -> None:
    rows = _fetch_all(PAGES, EVENTS)

    assert set(rows) == {"lensk", "s", "ivanovo"}
    assert rows["ivanovo"].active is False
    assert rows["ivanovo"].latitude == 57.00297


def test_coordinates_and_active_come_from_both_sources() -> None:
    rows = _fetch_all(PAGES, EVENTS)

    assert rows["lensk"].active is True
    assert rows["lensk"].longitude == 114.909692
    # Разъездная серия живёт только в pages.json — без координат, но в списке.
    assert rows["s"].latitude is None


def test_events_json_can_switch_a_page_off() -> None:
    """Строгое «и»: если хоть один источник говорит «не бежит», значит не бежит."""
    pages = [{"url": "https://s95.ru/events/ivanovo.json", "name": "Иваново", "active": True}]
    events = [{"code_name": "ivanovo", "name": "Иваново", "active": False}]

    rows = _fetch_all(pages, events)

    assert rows["ivanovo"].active is False


def test_broken_pages_endpoint_leaves_events_alone() -> None:
    with (
        patch("app.s95.api_client.S95_DOMAINS", ["https://s95.ru"]),
        patch("app.s95.api_client.fetch_pages", side_effect=RuntimeError("500")),
        patch("app.s95.api_client.fetch_events", return_value=EVENTS),
    ):
        rows = {row.slug: row for row in fetch_all_locations()}

    assert set(rows) == {"lensk", "ivanovo"}
