import pytest

from app.platform_adapters.five_verst.url import InvalidProfileUrlError, parse_profile_input


def test_parse_profile_input_accepts_numeric_code() -> None:
    parsed = parse_profile_input("790096427")
    assert parsed.user_id == "790096427"
    assert parsed.canonical_url == "https://5verst.ru/userstats/790096427/"


def test_parse_profile_input_accepts_barcode_latin_a() -> None:
    parsed = parse_profile_input("A790096427")
    assert parsed.user_id == "790096427"


def test_parse_profile_input_accepts_barcode_cyrillic_a() -> None:
    parsed = parse_profile_input("А790096427")
    assert parsed.user_id == "790096427"


def test_parse_profile_input_accepts_userstats_url() -> None:
    parsed = parse_profile_input("https://5verst.ru/userstats/12345/")
    assert parsed.user_id == "12345"


def test_parse_profile_input_rejects_garbage() -> None:
    with pytest.raises(InvalidProfileUrlError):
        parse_profile_input("hello-world")
