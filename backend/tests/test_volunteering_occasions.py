from __future__ import annotations

from datetime import date

from app.volunteering_occasions import (
    count_volunteering_for_platform,
    count_volunteering_occasions,
    volunteering_by_month_for_platform,
    volunteering_occasions_by_month,
)


def test_count_one_occasion_per_day_multiple_roles() -> None:
    rows = [
        (date(2026, 4, 4), "meshchersky"),
        (date(2026, 4, 4), "meshchersky"),
        (date(2026, 4, 4), "meshchersky"),
    ]
    assert count_volunteering_occasions(rows) == 1


def test_count_inventory_day_one_per_location() -> None:
    rows = [
        (date(2026, 1, 1), "meshchersky"),
        (date(2026, 1, 1), "meshchersky"),
        (date(2026, 1, 1), "sokolniki"),
    ]
    assert count_volunteering_occasions(rows) == 2


def test_count_inventory_day_same_location_different_years() -> None:
    rows = [
        (date(2024, 1, 1), "park850letiyamoskvy"),
        (date(2025, 1, 1), "park850letiyamoskvy"),
        (date(2024, 1, 1), "vernadskogo"),
    ]
    assert count_volunteering_occasions(rows) == 3


def test_count_same_day_different_locations_is_one_occasion() -> None:
    rows = [
        (date(2025, 10, 11), "petergofaleksandriysky"),
        (date(2025, 10, 11), "mikhalkovo"),
    ]
    assert count_volunteering_occasions(rows) == 1


def test_count_multiple_days() -> None:
    rows = [
        (date(2026, 4, 4), "meshchersky"),
        (date(2025, 12, 13), "meshchersky"),
        (date(2025, 12, 13), "meshchersky"),
    ]
    assert count_volunteering_occasions(rows) == 2


def test_occasions_by_month_inventory_day() -> None:
    rows = [
        (date(2026, 1, 1), "meshchersky"),
        (date(2026, 1, 1), "sokolniki"),
        (date(2026, 2, 7), "meshchersky"),
        (date(2026, 2, 7), "meshchersky"),
    ]
    assert dict(volunteering_occasions_by_month(rows)) == {"2026-01": 2, "2026-02": 1}


def test_five_verst_platform_uses_occasion_logic() -> None:
    rows = [
        (date(2026, 4, 4), "meshchersky"),
        (date(2026, 4, 4), "meshchersky"),
    ]
    assert count_volunteering_for_platform("five_verst", rows) == 1
    assert count_volunteering_for_platform("s95", rows) == 1


def test_non_five_verst_counts_unique_dates() -> None:
    rows = [
        (date(2026, 4, 4), "loc-a"),
        (date(2026, 4, 4), "loc-b"),
    ]
    assert count_volunteering_for_platform("parkrun", rows) == 1


def test_by_month_for_platform() -> None:
    rows = [
        (date(2026, 4, 4), "meshchersky"),
        (date(2026, 4, 4), "meshchersky"),
        (date(2026, 5, 2), "meshchersky"),
    ]
    assert dict(volunteering_by_month_for_platform("five_verst", rows)) == {
        "2026-04": 1,
        "2026-05": 1,
    }
