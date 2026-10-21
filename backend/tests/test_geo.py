from __future__ import annotations

from unittest.mock import patch

from app.geo.reverse_geocode import lookup_address, lookup_region


def test_lookup_region_prefers_state() -> None:
    with patch("app.geo.reverse_geocode._fetch_address", return_value={"state": "Москва", "country": "Россия"}):
        assert lookup_region(55.75, 37.62) == "Москва"


def test_lookup_address_fills_country_and_city() -> None:
    address = {
        "country": "Россия",
        "state": "Московская область",
        "city": "Мытищи",
    }
    with patch("app.geo.reverse_geocode._fetch_address", return_value=address):
        result = lookup_address(55.91, 37.74)
    assert result["country"] == "Россия"
    # normalize_region() срезает суффиксы « область» / « край» — это намеренно.
    assert result["region"] == "Московская"
    assert result["city"] == "Мытищи"


def test_lookup_address_asks_closer_when_town_is_missing() -> None:
    """Малый город («town» в OSM) Nominatim отдаёт только на zoom 12.

    Усть-Илимск на zoom 10 — лишь «городской округ Усть-Илимск», и локация
    осталась без города (репорт Егора 20.10.2026).
    """
    calls: list[int] = []

    def fake_fetch(latitude: float, longitude: float, *, zoom: int = 10) -> dict[str, str]:
        calls.append(zoom)
        base = {"country": "Россия", "state": "Иркутская область", "county": "городской округ Усть-Илимск"}
        return {**base, "town": "Усть-Илимск"} if zoom == 12 else base

    with patch("app.geo.reverse_geocode._fetch_address", side_effect=fake_fetch):
        result = lookup_address(57.95, 102.73)
    assert result["city"] == "Усть-Илимск"
    assert result["region"] == "Иркутская"
    assert calls == [10, 12]

    # Город нашёлся сразу — второго запроса нет.
    calls.clear()
    with patch("app.geo.reverse_geocode._fetch_address", side_effect=lambda *a, **k: (calls.append(k.get("zoom", 10)) or {"city": "Мытищи"})):
        assert lookup_address(55.91, 37.74)["city"] == "Мытищи"
    assert calls == [10]
