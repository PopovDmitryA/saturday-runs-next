from __future__ import annotations

from datetime import date

from app.activity_url import resolve_activity_url


def test_five_verst_builds_results_url() -> None:
    url = resolve_activity_url(
        platform_code="five_verst",
        event_date=date(2022, 10, 10),
        event_number=42,
        event_source_url=None,
        location_external_key="babushkinskynayauze",
    )
    assert url == "https://5verst.ru/babushkinskynayauze/results/10.10.2022/"


def test_five_verst_prefers_stored_protocol_url() -> None:
    stored = "https://5verst.ru/testpark/results/10.10.2022/"
    url = resolve_activity_url(
        platform_code="five_verst",
        event_date=date(2022, 10, 10),
        event_number=None,
        event_source_url=stored,
        location_external_key="testpark",
    )
    assert url == stored


def test_s95_only_uses_stored_protocol_url() -> None:
    stored = "https://s95.ru/events/100/protocol/"
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2025, 4, 12),
        event_number=100,
        event_source_url=stored,
        location_external_key="penza",
    )
    assert url == stored


def test_s95_accepts_activities_protocol_url() -> None:
    stored = "https://s95.ru/activities/4236"
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2026, 5, 23),
        event_number=166,
        event_source_url=stored,
        location_external_key="troitsk",
    )
    assert url == stored


def test_s95_without_protocol_url_returns_none() -> None:
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2025, 4, 12),
        event_number=100,
        event_source_url="https://s95.ru/events/penza",
        location_external_key="penza",
    )
    assert url is None


def test_parkrun_uses_all_results_url() -> None:
    profile = "https://www.parkrun.org.uk/parkrunner/7035519/"
    url = resolve_activity_url(
        platform_code="parkrun",
        event_date=date(2024, 1, 6),
        event_number=500,
        event_source_url="https://www.parkrun.org.uk/bushy/results/500/",
        location_external_key="bushy",
        profile_url=profile,
    )
    assert url == "https://www.parkrun.org.uk/parkrunner/7035519/all/"
