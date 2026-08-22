import pytest

from app.platform_adapters.s95.url import (
    InvalidProfileUrlError,
    is_valid_profile_url,
    parse_profile_input,
)


def test_parse_profile_input_accepts_athlete_id() -> None:
    parsed = parse_profile_input("5207")
    assert parsed.external_user_id == "5207"
    assert parsed.canonical_url == "https://s95.ru/athletes/5207/"


def test_parse_profile_input_accepts_athlete_id_with_spaces() -> None:
    assert parse_profile_input("  5207  ").external_user_id == "5207"


def test_parse_profile_input_accepts_url() -> None:
    parsed = parse_profile_input("https://s95.ru/athletes/5207/")
    assert parsed.external_user_id == "5207"
    assert parsed.canonical_url == "https://s95.ru/athletes/5207/"


def test_parse_profile_input_accepts_url_without_scheme() -> None:
    parsed = parse_profile_input("s95.ru/athletes/5207")
    assert parsed.canonical_url == "https://s95.ru/athletes/5207/"


def test_parse_profile_input_keeps_regional_domain() -> None:
    parsed = parse_profile_input("https://s95.rs/athletes/42/")
    assert parsed.domain == "s95.rs"
    assert parsed.canonical_url == "https://s95.rs/athletes/42/"


def test_parse_profile_input_rejects_barcode_with_hint() -> None:
    # Штрихкод в профиле С95 — parkrun-код, по нему атлета на s95.ru не найти.
    with pytest.raises(InvalidProfileUrlError, match="штрихкод"):
        parse_profile_input("A7035519")


def test_parse_profile_input_rejects_garbage_with_format_hint() -> None:
    with pytest.raises(InvalidProfileUrlError, match="ID участника"):
        parse_profile_input("hello")


def test_parse_profile_input_rejects_foreign_domain() -> None:
    with pytest.raises(InvalidProfileUrlError):
        parse_profile_input("https://example.com/athletes/5207/")


def test_is_valid_profile_url_accepts_athlete_id() -> None:
    assert is_valid_profile_url("5207")
    assert is_valid_profile_url("https://s95.ru/athletes/5207/")
    assert not is_valid_profile_url("A7035519")
