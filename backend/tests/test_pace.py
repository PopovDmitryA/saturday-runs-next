from app.pace import (
    format_pace_display,
    pace_from_finish_time_sec,
    parse_pace_value,
    resolve_run_pace,
)


def test_parse_pace_value() -> None:
    sec, display = parse_pace_value("4:44")
    assert sec == 4 * 60 + 44
    assert display == "4:44"


def test_parse_pace_rejects_finish_like_values() -> None:
    assert parse_pace_value("23:41") == (None, None)


def test_pace_from_finish_time_five_km() -> None:
    finish_sec = 23 * 60 + 41
    sec, display = pace_from_finish_time_sec(finish_sec)
    assert sec == 284
    assert display == "4:44"


def test_resolve_prefers_site_pace() -> None:
    sec, display = resolve_run_pace(284, "4:44", 23 * 60 + 41)
    assert sec == 284
    assert display == "4:44"


def test_resolve_calculates_when_missing() -> None:
    sec, display = resolve_run_pace(None, None, 23 * 60 + 42)
    assert sec == 284
    assert display == format_pace_display(284)
