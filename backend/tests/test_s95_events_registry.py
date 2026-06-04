from __future__ import annotations

from pathlib import Path

from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.bulk_parser import LocationRegistryStatus
from app.s95.parsers.events_registry import (
    S95LocationRegistryStatus,
    parse_events_registry_html,
    registry_entry_is_cancelled,
    registry_entry_is_paused,
    slug_from_href,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "s95_events_registry.html"


def test_s95_slug_from_href() -> None:
    assert slug_from_href("/events/angarskie_prudy", "https://s95.ru") == "angarskie_prudy"
    assert slug_from_href("https://s95.ru/events/gatchina", "https://s95.ru") == "gatchina"
    assert slug_from_href("/events/events", "https://s95.ru") is None


def test_parse_s95_events_registry_fixture() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    page = parse_events_registry_html(html, "https://s95.ru/events")

    by_slug = {entry.slug: entry for entry in page.entries}
    assert by_slug["angarskie_prudy"].status == S95LocationRegistryStatus.active
    assert by_slug["gatchina"].status == S95LocationRegistryStatus.cancelled
    assert by_slug["valday"].status == S95LocationRegistryStatus.paused
    assert by_slug["angarskie_prudy"].name == "Ангарские пруды"


def test_s95_registry_status_helpers() -> None:
    assert registry_entry_is_cancelled(S95LocationRegistryStatus.cancelled) is True
    assert registry_entry_is_cancelled(S95LocationRegistryStatus.active) is False
    assert registry_entry_is_paused(S95LocationRegistryStatus.paused) is True
    assert registry_entry_is_paused(S95LocationRegistryStatus.cancelled) is False


def test_five_verst_registry_cancelled_is_separate_from_paused() -> None:
    assert bulk_parser.registry_entry_is_cancelled(LocationRegistryStatus.cancelled) is True
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.cancelled) is False
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.paused) is True
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.preparing) is True
