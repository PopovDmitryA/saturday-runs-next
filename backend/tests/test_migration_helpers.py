from __future__ import annotations

import datetime as dt
from datetime import date

from app.migration.helpers import (
    five_verst_run_key,
    five_verst_volunteer_key,
    legacy_time_to_seconds,
    legacy_timestamp_to_date,
    slug_from_5verst_url,
    slug_from_s95_url,
    slugify_parkrun_name,
)


def test_slug_from_5verst_url() -> None:
    assert slug_from_5verst_url("https://5verst.ru/babushkinskynayauze/") == "babushkinskynayauze"
    assert slug_from_5verst_url("https://5verst.ru/events/") is None


def test_slug_from_s95_url() -> None:
    assert slug_from_s95_url("https://s95.ru/events/izmailovo") == "izmailovo"
    assert slug_from_s95_url("https://s95.by/events/parkzhrun/") == "parkzhrun"
    assert slug_from_s95_url("https://s95.rs/events/belgrade") == "belgrade"


def test_is_five_verst_unknown_runner() -> None:
    from app.migration.helpers import is_five_verst_unknown_runner, five_verst_unknown_user_id

    assert is_five_verst_unknown_runner(
        user_id=None,
        name_runner="НЕИЗВЕСТНЫЙ",
        status_runner="unknown_runner",
    )
    assert not is_five_verst_unknown_runner(
        user_id="123",
        name_runner="НЕИЗВЕСТНЫЙ",
        status_runner="unknown_runner",
    )
    assert (
        five_verst_unknown_user_id("zatyumenskiy", date(2022, 7, 9), 50)
        == "unknown:zatyumenskiy:2022-07-09:50"
    )


def test_legacy_time_to_seconds() -> None:
    assert legacy_time_to_seconds(dt.time(0, 23, 45)) == 23 * 60 + 45
    assert legacy_time_to_seconds("23:45") == 23 * 60 + 45
    assert legacy_time_to_seconds("01:02:03") == 3723


def test_legacy_timestamp_to_date() -> None:
    assert legacy_timestamp_to_date(dt.datetime(2024, 5, 18, 9, 0)) == date(2024, 5, 18)
    assert legacy_timestamp_to_date("2024-05-18") == date(2024, 5, 18)


def test_five_verst_keys() -> None:
    slug = "babushkinskynayauze"
    event_date = date(2024, 5, 18)
    user_id = "12345"
    assert five_verst_run_key(slug, event_date, user_id) == "babushkinskynayauze:2024-05-18:12345"
    assert (
        five_verst_volunteer_key(slug, event_date, user_id, "Сканер")
        == "babushkinskynayauze:2024-05-18:vol:12345:сканер"
    )


def test_slugify_parkrun_name() -> None:
    assert slugify_parkrun_name("Babushkinsky na Yauze") == "babushkinsky-na-yauze"
