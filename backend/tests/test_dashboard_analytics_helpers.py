from datetime import date

from app.models import Location
from app.services.dashboard_service import (
    _avg_vs_field_pct,
    _collect_field_comparison_pairs,
    _earned_run_clubs,
    _next_run_club,
    _next_run_milestone,
    _resolve_field_avg_sec,
    _saturday_consistency,
)
from app.services.user_location_stats import count_unique_geo_from_rows


def test_next_run_milestone() -> None:
    assert _next_run_milestone(0) == (10, 10)
    assert _next_run_milestone(9) == (10, 1)
    assert _next_run_milestone(50) == (100, 50)
    assert _next_run_milestone(1000) == (None, None)


def test_run_clubs() -> None:
    assert _earned_run_clubs(49) == []
    assert _earned_run_clubs(50) == [50]
    assert _earned_run_clubs(150) == [50, 100]
    assert _next_run_club(150) == 250


def test_avg_vs_field_pct() -> None:
    assert _avg_vs_field_pct([(1139, 1200)]) == 5.1
    assert _avg_vs_field_pct([]) is None


def test_resolve_field_avg_sec_prefers_summary() -> None:
    assert _resolve_field_avg_sec(1200, 1100, 50) == 1200
    assert _resolve_field_avg_sec(None, 1100, 3) == 1100
    assert _resolve_field_avg_sec(None, 1100, 1) is None


def test_collect_field_comparison_pairs() -> None:
    pairs = _collect_field_comparison_pairs([(1000, None, 1100, 3), (1000, None, 1000, 1)])
    assert pairs == [(1000, 1100)]
    assert _avg_vs_field_pct(pairs) == 9.1


def test_saturday_consistency() -> None:
    today = date(2026, 5, 27)  # Wednesday
    activity_dates = {date(2026, 5, 23), date(2026, 5, 16)}
    pct, active, total = _saturday_consistency(activity_dates, today)
    assert active == 2
    assert total >= 50
    assert pct == round(active / total * 100, 1)


def test_count_unique_geo_merges_moscow_region_variants() -> None:
    loc_mo = Location(
        platform_id=__import__("uuid").uuid4(),
        external_key="a",
        name="A",
        region="Московская область",
        city="Мытищи",
    )
    loc_m = Location(
        platform_id=__import__("uuid").uuid4(),
        external_key="b",
        name="B",
        region="Московская",
        city="Королёв",
    )
    regions, cities = count_unique_geo_from_rows([(loc_mo, "five_verst"), (loc_m, "five_verst")])
    assert regions == 1
    assert cities == 2
