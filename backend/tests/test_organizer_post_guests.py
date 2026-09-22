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
    guests = sort_guest_homes([
        _guest("Яковлев Пётр", "Сокольники"),
        _guest("Абрамов Иван", "Вернадского", "Москва"),
    ])
    text = _stats_post(svod, guests, names_layout="lines")
    assert "🧳 Гости локации (2):" in text
    guest_lines = [line for line in text.splitlines() if line.startswith("• ")]
    assert guest_lines == [
        "• Абрамов Иван — Вернадского (Москва)",
        "• Яковлев Пётр — Сокольники",
    ]
    # В строку — через «;»: запятая внутри «Имя — Дом (Город)» читалась бы
    # как разделитель.
    inline = _stats_post(svod, guests, names_layout="inline")
    assert "🧳 Гости локации (2): Абрамов Иван — Вернадского (Москва); Яковлев Пётр — Сокольники." in inline


def _svod() -> dict[str, object]:
    return {
        "event": {
            "location_name": "Мещерский",
            "event_number": 101,
            "event_date": date(2026, 9, 19),
            "finishers_count": 90,
            "volunteers_count": 10,
            "platform_name": "5 вёрст",
        },
        "runners": [
            {"name": "Александр БЕЛОВ", "is_pb": False, "first_in_system": True, "location_milestone": None},
            {"name": "Артем КИПТИК", "is_pb": True, "first_in_system": True, "location_milestone": None},
            {"name": "Старожил СТАРЫЙ", "is_pb": True, "first_in_system": False, "location_milestone": 50},
        ],
    }


def test_stats_post_names_per_line() -> None:
    """«Имена построчно» (просьба организаторов через Дмитрия 21.09.2026):
    каждый именной блок — заголовок и по имени на строку, хвост-фраза после
    списка."""
    lines = _stats_post(_svod(), names_layout="lines").splitlines()
    start = lines.index("👑 Первый раз на старте (2):")
    assert lines[start + 1 : start + 4] == [
        "• Александр БЕЛОВ",
        "• Артем КИПТИК",
        "Добро пожаловать в беговую семью!",
    ]
    # Новичок с ПБ в личниках не дублируется; личники тоже построчно.
    start = lines.index("🚀 Личные рекорды обновили (1):")
    assert lines[start + 1 : start + 3] == ["• Старожил СТАРЫЙ", "Гордимся каждым новым максимумом!"]
    assert lines[lines.index("🎂 Юбилеи:") + 1] == "• Старожил СТАРЫЙ — 50-й финиш здесь"


def test_stats_post_names_inline_is_the_old_wording() -> None:
    """Раскладка «в строку» — прежний текст слово в слово."""
    text = _stats_post(_svod(), names_layout="inline")
    assert "👑 Первый раз на старте (2): Александр БЕЛОВ, Артем КИПТИК. Добро пожаловать в беговую семью!" in text
    assert "🚀 Личные рекорды обновили (1): Старожил СТАРЫЙ. Гордимся каждым новым максимумом!" in text
    assert "🎂 Юбилеи: Старожил СТАРЫЙ — 50-й финиш здесь." in text
    assert "• " not in text
