from app.services.dashboard_service import _format_volunteering_index


def test_volunteering_index_empty_activity() -> None:
    assert _format_volunteering_index(0, 0) is None


def test_volunteering_index_only_volunteering() -> None:
    assert _format_volunteering_index(0, 5) == "100%+"


def test_volunteering_index_only_runs() -> None:
    assert _format_volunteering_index(10, 0) == "0%"


def test_volunteering_index_below_one() -> None:
    assert _format_volunteering_index(10, 3) == "30%"
    assert _format_volunteering_index(4, 1) == "25%"


def test_volunteering_index_exactly_one() -> None:
    assert _format_volunteering_index(5, 5) == "100%"


def test_volunteering_index_above_one() -> None:
    assert _format_volunteering_index(5, 10) == "100%+"
