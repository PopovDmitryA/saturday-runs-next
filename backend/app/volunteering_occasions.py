from __future__ import annotations

from collections import Counter
from datetime import date


def is_inventory_day(event_date: date) -> bool:
    """5 вёрст: 1 января — первая инвентаризация, зачёт по локациям."""
    return event_date.month == 1 and event_date.day == 1


def count_volunteering_occasions(rows: list[tuple[date, str]]) -> int:
    """One volunteering per calendar day; inventory day counts one per location."""
    inventory_locations: set[str] = set()
    regular_dates: set[date] = set()
    for event_date, location_key in rows:
        if is_inventory_day(event_date):
            inventory_locations.add(location_key or "unknown")
        else:
            regular_dates.add(event_date)
    return len(regular_dates) + len(inventory_locations)


def volunteering_occasions_by_month(rows: list[tuple[date, str]]) -> Counter[str]:
    inventory_locations_by_date: dict[date, set[str]] = {}
    regular_dates: set[date] = set()
    for event_date, location_key in rows:
        if is_inventory_day(event_date):
            inventory_locations_by_date.setdefault(event_date, set()).add(location_key or "unknown")
        else:
            regular_dates.add(event_date)

    counter: Counter[str] = Counter()
    for event_date in regular_dates:
        counter[_month_key(event_date)] += 1
    for event_date, locations in inventory_locations_by_date.items():
        counter[_month_key(event_date)] += len(locations)
    return counter


def count_volunteering_for_platform(
    platform_code: str,
    rows: list[tuple[date, str]],
) -> int:
    if platform_code == "five_verst":
        return count_volunteering_occasions(rows)
    return len({event_date for event_date, _ in rows})


def volunteering_by_month_for_platform(
    platform_code: str,
    rows: list[tuple[date, str]],
) -> Counter[str]:
    if platform_code == "five_verst":
        return volunteering_occasions_by_month(rows)
    counter: Counter[str] = Counter()
    for event_date in {event_date for event_date, _ in rows}:
        counter[_month_key(event_date)] += 1
    return counter


def volunteer_occasion_dates(
    platform_code: str,
    rows: list[tuple[date, str]],
) -> set[date]:
    if platform_code == "five_verst":
        inventory_dates: set[date] = set()
        regular_dates: set[date] = set()
        for event_date, location_key in rows:
            if is_inventory_day(event_date):
                inventory_dates.add(event_date)
            else:
                regular_dates.add(event_date)
        return regular_dates | inventory_dates
    return {event_date for event_date, _ in rows}


def _month_key(value: date) -> str:
    return f"{value.year}-{value.month:02d}"
