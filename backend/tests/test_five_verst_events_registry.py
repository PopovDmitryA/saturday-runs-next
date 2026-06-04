from __future__ import annotations

from pathlib import Path

from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.bulk_parser import LocationRegistryStatus

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "five_verst_events_fragment.html"


def test_slug_from_href() -> None:
    assert bulk_parser.slug_from_href("https://5verst.ru/babushkinskynayauze/") == "babushkinskynayauze"
    assert bulk_parser.slug_from_href("https://5verst.ru/park10letiyaangarska") == "park10letiyaangarska"
    assert bulk_parser.slug_from_href("https://5verst.ru/events/") is None
    assert bulk_parser.slug_from_href("https://5verst.ru/results/latest/") is None


def test_parse_events_page_html_fixture() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    page = bulk_parser.parse_events_page_html(html)

    assert len(page.entries) > 100
    slugs = {entry.slug for entry in page.entries}
    assert "belgorodparkpobedy" in slugs
    assert "babushkinskynayauze" in slugs
    assert "aleksandrovsosenki" in slugs

    by_slug = {entry.slug: entry for entry in page.entries}
    assert by_slug["izumrudnyy"].status == LocationRegistryStatus.cancelled
    assert by_slug["izumrudnyy"].name == "Барнаул"
    assert by_slug["memorialnypark"].status == LocationRegistryStatus.paused
    assert by_slug["okkervil"].status == LocationRegistryStatus.preparing
    assert by_slug["belgorodparkpobedy"].status == LocationRegistryStatus.active

    assert len(page.saturday_cancellations) >= 2
    cancel_slugs = {entry.slug for entry in page.saturday_cancellations}
    assert "izumrudnyy" in cancel_slugs


def test_registry_entry_is_paused() -> None:
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.active) is False
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.paused) is True
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.cancelled) is False
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.preparing) is True
