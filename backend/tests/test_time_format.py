from app.time_format import format_finish_time_display, normalize_finish_time_display, parse_finish_time


def test_format_finish_time_display() -> None:
    assert format_finish_time_display(25 * 60 + 30) == "00:25:30"
    assert format_finish_time_display(3661) == "01:01:01"


def test_parse_finish_time_mm_ss() -> None:
    sec, display = parse_finish_time("25:30")
    assert sec == 25 * 60 + 30
    assert display == "00:25:30"


def test_parse_finish_time_hms() -> None:
    sec, display = parse_finish_time("1:01:01")
    assert sec == 3661
    assert display == "01:01:01"


def test_normalize_from_seconds() -> None:
    assert normalize_finish_time_display(1530, "25:30") == "00:25:30"
