from __future__ import annotations

from app.location_page_url import location_page_url, pick_primary_location_url


def test_location_page_url_canonical_by_platform() -> None:
    assert location_page_url("five_verst", "pushkin") == "https://5verst.ru/pushkin/"
    assert location_page_url("s95", "sokolniki") == "https://s95.ru/events/sokolniki"
    assert location_page_url("parkrun", "richmond") == "https://www.parkrun.org.uk/richmond/"
    assert location_page_url("runpark", "moscow") == "https://runpark.ru/Places/moscow"


def test_location_page_url_prefers_canonical_over_results_source() -> None:
    assert (
        location_page_url("five_verst", "pushkin", "https://5verst.ru/pushkin/results/123/")
        == "https://5verst.ru/pushkin/"
    )


def test_location_page_url_uses_clean_source_url() -> None:
    assert (
        location_page_url("five_verst", "pushkin", "https://5verst.ru/pushkin/")
        == "https://5verst.ru/pushkin/"
    )


def test_location_page_url_ignores_results_source_without_canonical() -> None:
    assert (
        location_page_url("five_verst", "unknown", "https://5verst.ru/pushkin/results/123/")
        == "https://5verst.ru/pushkin/results/123/"
    )


def test_location_page_url_unknown_slug_without_source() -> None:
    assert location_page_url("five_verst", "unknown") is None
    assert location_page_url("five_verst", "") is None


def test_pick_primary_location_url_prefers_active_platform() -> None:
    urls = {
        "five_verst": "https://5verst.ru/pushkin/",
        "s95": "https://s95.ru/events/pushkin",
    }
    assert (
        pick_primary_location_url(
            ["five_verst", "s95"],
            active_platform="s95",
            urls_by_platform=urls,
        )
        == "https://s95.ru/events/pushkin"
    )


def test_pick_primary_location_url_uses_platform_order() -> None:
    urls = {
        "parkrun": "https://www.parkrun.org.uk/richmond/",
        "five_verst": "https://5verst.ru/pushkin/",
    }
    assert (
        pick_primary_location_url(["parkrun", "five_verst"], urls_by_platform=urls)
        == "https://5verst.ru/pushkin/"
    )
