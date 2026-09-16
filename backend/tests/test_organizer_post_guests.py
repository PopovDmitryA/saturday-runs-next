from __future__ import annotations

from datetime import date

from app.services.organizer_post_service import _stats_post, sort_guest_homes


def _guest(name: str, home: str, city: str | None = None) -> dict[str, object]:
    return {"name": name, "home_name": home, "home_city": city}


def test_sort_guest_homes_orders_by_home_park() -> None:
    guests = [
        _guest("Яковлев Пётр", "Вернадского"),
        _guest("Абрамов Иван", "Сокольники"),
        _guest("Белкин Семён", "Вернадского"),
    ]
    assert [item["name"] for item in sort_guest_homes(guests)] == [
        "Белкин Семён",
        "Яковлев Пётр",
        "Абрамов Иван",
    ]


def test_sort_guest_homes_survives_missing_city() -> None:
    guests = [_guest("Иванов", "Битца"), _guest("Петров", "Битца", "Москва")]
    assert [item["name"] for item in sort_guest_homes(guests)] == ["Иванов", "Петров"]


def test_stats_post_lists_guests_in_given_order() -> None:
    svod = {
        "event": {
            "location_name": "Мещерский",
            "event_number": 100,
            "event_date": date(2026, 9, 12),
            "finishers_count": 120,
            "volunteers_count": 12,
        },
        "runners": [],
    }
    text = _stats_post(svod, sort_guest_homes([
        _guest("Яковлев Пётр", "Сокольники"),
        _guest("Абрамов Иван", "Вернадского", "Москва"),
    ]))
    assert "🧳 Гости локации (2):" in text
    guest_lines = [line for line in text.splitlines() if line.startswith("• ")]
    assert guest_lines == [
        "• Абрамов Иван — Вернадского (Москва)",
        "• Яковлев Пётр — Сокольники",
    ]
