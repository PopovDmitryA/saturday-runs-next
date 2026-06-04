import pytest

from app.platform_adapters.parkrun.url import InvalidProfileUrlError, parse_profile_input, parse_profile_url


def test_parse_parkrun_profile_url() -> None:
    parsed = parse_profile_url("https://www.parkrun.org.uk/parkrunner/7035519/all/")
    assert parsed.athlete_id == "7035519"
    assert parsed.profile_url.endswith("/parkrunner/7035519/")
    assert parsed.all_results_url.endswith("/parkrunner/7035519/all/")


def test_parse_parkrun_barcode_input() -> None:
    parsed = parse_profile_input("A7035519")
    assert parsed.athlete_id == "7035519"


def test_parse_parkrun_barcode_cyrillic_a() -> None:
    parsed = parse_profile_input("А7035519")
    assert parsed.athlete_id == "7035519"


def test_parse_parkrun_barcode_digits_only() -> None:
    parsed = parse_profile_input("7035519")
    assert parsed.athlete_id == "7035519"


def test_reject_non_uk_host() -> None:
    with pytest.raises(InvalidProfileUrlError):
        parse_profile_url("https://www.parkrun.ru/parkrunner/1/")
