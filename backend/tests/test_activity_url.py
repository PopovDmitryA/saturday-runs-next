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


def test_s95_builds_human_readable_results_url_ignoring_stored_json_api_link() -> None:
    """source_url на s95-событиях после перехода на JSON API часто хранит

    технический .../activities/{id}.json (нужен для внутреннего дозапроса
    протокола, не для показа пользователю) — всегда строим человекочитаемую
    страницу результатов по slug локации и дате, как и для five_verst.
    """
    stored = "https://s95.ru/activities/3022.json"
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2025, 4, 12),
        event_number=100,
        event_source_url=stored,
        location_external_key="penza",
    )
    assert url == "https://s95.ru/events/penza/results/12.04.2025/"


def test_s95_builds_results_url_even_with_legacy_protocol_stored() -> None:
    stored = "https://s95.ru/events/100/protocol/"
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2026, 5, 23),
        event_number=166,
        event_source_url=stored,
        location_external_key="troitsk",
    )
    assert url == "https://s95.ru/events/troitsk/results/23.05.2026/"


def test_s95_without_location_slug_falls_back_to_stored_protocol_url() -> None:
    stored = "https://s95.ru/activities/460"
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2025, 4, 12),
        event_number=100,
        event_source_url=stored,
        location_external_key="unknown",
    )
    assert url == stored


def test_s95_without_location_slug_or_protocol_url_returns_none() -> None:
    url = resolve_activity_url(
        platform_code="s95",
        event_date=date(2025, 4, 12),
        event_number=100,
        event_source_url="https://s95.ru/events/penza",
        location_external_key="unknown",
    )
    assert url is None


def test_prefer_event_source_url_keeps_s95_activity_link() -> None:
    from app.activity_url import prefer_event_source_url

    assert (
        prefer_event_source_url(
            "s95",
            "https://s95.ru/activities/460",
            "https://s95.ru/events/kuzminki",
        )
        == "https://s95.ru/activities/460"
    )


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
