"""Локации рядом для бота: правило выбора, тексты, журнал «где ищут старт»."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.nearby_locations_service import (
    NEARBY_RADIUS_KM,
    MyVisits,
    _runs_word,
    format_km,
    format_nearby_message,
    nearby_summary,
    pick_nearby,
    search_by_name,
    start_line,
)
from app.services.nearby_query_log_service import cell_of

# Центр Москвы (Красная площадь) и точки на нужных расстояниях к северу:
# градус широты ≈ 111.2 км.
MOSCOW = (55.7558, 37.6173)
KM_PER_DEGREE = 111.195


def _point(key: str, km_north: float, **flags: Any) -> dict[str, object]:
    return {
        "catalog_identity_key": key,
        "name": key.title(),
        "city": flags.pop("city", "Москва"),
        "latitude": MOSCOW[0] + km_north / KM_PER_DEGREE,
        "longitude": MOSCOW[1],
        "location_slug": key,
        "platform_codes": ["five_verst"],
        "active_platform": "five_verst",
        "is_paused": flags.pop("is_paused", False),
        "is_cancelled": flags.pop("is_cancelled", False),
        "is_upcoming": flags.pop("is_upcoming", False),
    }


def _keys(points: list[dict[str, object]], origin: tuple[float, float] = MOSCOW) -> list[str]:
    picks, _has_nearby = pick_nearby(points, origin)
    return [str(pick.point["catalog_identity_key"]) for pick in picks]


def test_three_nearest_within_radius_sorted_by_distance() -> None:
    points = [_point("far", 12), _point("a", 3), _point("c", 6), _point("b", 5), _point("d", 7)]
    picks, has_nearby = pick_nearby(points, MOSCOW)
    assert has_nearby is True
    assert [pick.point["catalog_identity_key"] for pick in picks] == ["a", "b", "c"]
    assert round(picks[0].distance_km, 1) == 3.0


def test_single_location_in_radius_is_shown_alone() -> None:
    """Казань: одна локация в городе, следующая за 24 км — показываем одну."""
    points = [_point("kazan", 2), _point("innopolis", 24)]
    assert _keys(points) == ["kazan"]


def test_nothing_in_radius_gives_one_nearest_however_far() -> None:
    points = [_point("khabarovsk", 644), _point("sakhalin", 954)]
    picks, has_nearby = pick_nearby(points, MOSCOW)
    assert has_nearby is False
    assert [pick.point["catalog_identity_key"] for pick in picks] == ["khabarovsk"]


def test_paused_locations_are_never_shown() -> None:
    points = [_point("paused", 1, is_paused=True), _point("live", 4)]
    assert _keys(points) == ["live"]


def test_cancelled_stays_but_does_not_take_a_slot() -> None:
    points = [_point("cancelled", 1, is_cancelled=True), _point("a", 3), _point("b", 5), _point("c", 6)]
    assert _keys(points) == ["cancelled", "a", "b", "c"]


def test_upcoming_does_not_take_a_slot_either() -> None:
    points = [_point("soon", 2, is_upcoming=True), _point("a", 3), _point("b", 5), _point("c", 6)]
    assert _keys(points) == ["soon", "a", "b", "c"]


def test_only_cancelled_in_radius_adds_nearest_live_beyond_it() -> None:
    """Идти некуда — выходим за радиус ровно за одной действующей."""
    points = [_point("cancelled", 2, is_cancelled=True), _point("live", 40), _point("live2", 45)]
    picks, has_nearby = pick_nearby(points, MOSCOW)
    assert has_nearby is True
    assert [pick.point["catalog_identity_key"] for pick in picks] == ["cancelled", "live"]


def test_beyond_radius_not_added_when_there_is_somewhere_to_go() -> None:
    points = [_point("a", 3), _point("b", 5), _point("far", 16)]
    assert _keys(points) == ["a", "b"]


def test_search_by_name_folds_yo_and_matches_city() -> None:
    points = [
        _point("seleznevka", 3) | {"name": "Селезнёвка"},
        _point("kazan", 700, city="Казань") | {"name": "Центральный парк"},
        _point("paused", 1, is_paused=True) | {"name": "Селезнёвка старая"},
    ]
    assert [pick.point["name"] for pick in search_by_name(points, "селезневка", None)] == ["Селезнёвка"]
    assert [pick.point["name"] for pick in search_by_name(points, "казань", MOSCOW)] == ["Центральный парк"]
    assert search_by_name(points, "  ", MOSCOW) == []


def test_cell_rounding_hides_exact_point() -> None:
    assert cell_of(55.7558, 37.6173) == (55.75, 37.6)
    assert cell_of(55.7781, 37.6449) == (55.8, 37.65)
    assert cell_of(-33.8688, 151.2093) == (-33.85, 151.2)


def test_km_and_runs_word() -> None:
    assert format_km(3.04) == "3 км"
    assert format_km(6.2) == "6,2 км"
    assert format_km(12.4) == "12 км"
    assert [_runs_word(n) for n in (1, 2, 5, 11, 12, 21, 22, 25)] == [
        "раз",
        "раза",
        "раз",
        "раз",
        "раз",
        "раз",
        "раза",
        "раз",
    ]


TODAY = date(2026, 10, 2)  # пятница


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "identity_key": "catalog:1",
        "name": "Сокольники",
        "distance_km": 3.0,
        "platform_code": "five_verst",
        "site_url": "https://run5k.run/locations/sokolniki",
        "status": "ok",
        "cancel_reason": None,
        "next_start": {
            "date": "2026-10-03",
            "number": 137,
            "platform_code": "five_verst",
            "start_time": "9:00",
            "challenge_title": "Нумератор",
            "plus_one": True,
        },
        "weather": {"icon": "☁️", "summary": "6°, облачно, ветер 4 м/с", "advice": "Прохладно — длинный рукав"},
        "address": "Москва, улица Шумкина, 1/26",
        "my_runs": 12,
        "my_last_run": "2026-09-26",
    }
    item.update(overrides)
    return item


def test_start_line_variants() -> None:
    assert start_line(_item(), TODAY) == "🗓 завтра, 9:00 · старт ≈№137"
    later = _item(next_start={**_item()["next_start"], "date": "2026-10-10", "start_time": None})
    assert start_line(later, TODAY) == "🗓 сб 10 октября · старт ≈№137"
    assert start_line(_item(status="cancelled", cancel_reason="Субботник в парке"), TODAY) == (
        "⚠️ Ближайший старт отменён: Субботник в парке"
    )
    assert start_line(_item(status="upcoming"), TODAY).startswith("🔜 Скоро открытие")
    assert start_line(_item(next_start=None), TODAY) is None


def test_message_for_linked_runner_shows_personal_lines() -> None:
    visits = MyVisits(runs={"catalog:1": (12, "2026-09-26")}, unique_run_locations=36, start_numbers=set())
    new_place = _item(identity_key="catalog:2", name="ЗИЛ", distance_km=6.2, my_runs=0, my_last_run=None)
    text = format_nearby_message([_item(), new_place], has_nearby=True, today=TODAY, visits=visits, linked=True)

    assert text.startswith("📍 Локации рядом с вами:")
    assert "1. [**Сокольники**](https://run5k.run/locations/sokolniki) — 3 км · 5 вёрст" in text
    assert "2. [**ЗИЛ**]" in text
    assert "🗓 завтра, 9:00 · старт ≈№137" in text
    assert "☁️ 6°, облачно, ветер 4 м/с · Прохладно — длинный рукав" in text
    assert "📌 Москва, улица Шумкина, 1/26" in text
    assert "✅ Вы бегали здесь 12 раз, последний — сб 26 сентября" in text
    assert "🆕 Здесь вы ещё не бегали — станет вашей 37-й локацией" in text
    assert "🎯 №137 — новый номер для челленджа «Нумератор»" in text
    assert "Войдите на run5k.run" not in text


def test_message_for_stranger_has_no_personal_but_has_hint() -> None:
    text = format_nearby_message([_item()], has_nearby=True, today=TODAY, visits=None, linked=False)
    assert text.startswith("📍 Ближайшая к вам локация:")
    # Одна локация — без номера.
    assert "\n\n[**Сокольники**]" in text
    assert "Вы бегали" not in text
    assert "🎯" not in text
    assert "Войдите на run5k.run через Telegram" in text


def test_message_when_nothing_nearby() -> None:
    far = _item(name="Хабаровск", distance_km=644.0, my_runs=None)
    text = format_nearby_message([far], has_nearby=False, today=TODAY, visits=None, linked=True)
    assert text.startswith(f"📍 В радиусе {int(NEARBY_RADIUS_KM)} км стартов нет. Ближайшая локация:")
    assert "— 644 км · 5 вёрст" in text


def test_summary_for_the_log() -> None:
    items = [_item(distance_km=3.0), _item(identity_key="catalog:2", distance_km=40.0)]
    assert nearby_summary(items, linked=True) == {
        "linked": True,
        "nearest_identity_key": "catalog:1",
        "nearest_distance_km": 3.0,
        "within_radius": 1,
    }
    assert nearby_summary([], linked=False)["nearest_identity_key"] is None


def test_address_drops_five_verst_preamble() -> None:
    from app.services.nearby_locations_service import _short_address

    assert _short_address("Мероприятие проводится в парке Сокольники в Москве. Москва, Сокольнический Вал, 1с1") == (
        "Москва, Сокольнический Вал, 1с1"
    )
    # Одно предложение — оставляем как есть, иначе адреса не останется вовсе.
    assert _short_address("Мероприятие проводится: казанском Центральном парке") == (
        "Мероприятие проводится: казанском Центральном парке"
    )
    assert _short_address("Лесопарк Пышки, нижняя парковка") == "Лесопарк Пышки, нижняя парковка"
    assert _short_address(None) is None
    long = "Сбор у главного входа " * 10
    assert len(_short_address(long) or "") <= 121
