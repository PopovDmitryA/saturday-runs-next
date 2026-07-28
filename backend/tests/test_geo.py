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
