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
    # Причина из блока отмен доезжает и до записи в общем списке площадок:
    # именно её мы кладём в location.cancel_reason.
    assert by_slug["izumrudnyy"].cancel_reason == "test"
    assert by_slug["park10letiyaangarska"].cancel_reason is None


def test_parse_events_page_html_reads_cancel_reasons() -> None:
    # Разметка 5 вёрст в живую (16.09.2026): площадки в блоке отмен разделены
    # <br>, причина есть не у всех.
    html = """
    <div class="cancel-list">
    <a href="https://5verst.ru/olimpiyskayaalleya">Пенза</a> отменён по причине:
    Проведение регионального мероприятия в это время.<br>
    <a href="https://5verst.ru/gubernskypark">Калуга</a> отменён<br></div>
    <div class='events-columns'>
    <div class="event-block"><ul><h4>Пенза</h4>
    <li><a href="https://5verst.ru/olimpiyskayaalleya">Пенза</a> (отмена)</li>
    </ul></div>
    <div class="event-block"><ul><h4>Калуга</h4>
    <li><a href="https://5verst.ru/gubernskypark">Калуга</a> (отмена)</li>
    </ul></div>
    </div>
    """
    page = bulk_parser.parse_events_page_html(html)
    by_slug = {entry.slug: entry for entry in page.entries}
    assert (
        by_slug["olimpiyskayaalleya"].cancel_reason
        == "Проведение регионального мероприятия в это время"
    )
    # Причина соседа не утекает к площадке, которая её не назвала.
    assert by_slug["gubernskypark"].cancel_reason is None
    by_cancel = {entry.slug: entry for entry in page.saturday_cancellations}
    assert by_cancel["olimpiyskayaalleya"].cancel_reason is not None


def test_registry_entry_is_paused() -> None:
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.active) is False
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.paused) is True
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.cancelled) is False
    # «Скоро» — не пауза: у площадки старты ещё не начинались, а у паузы уже
    # кончились (решение Дмитрия 20.08.2026).
    assert bulk_parser.registry_entry_is_paused(LocationRegistryStatus.preparing) is False
    assert bulk_parser.registry_entry_is_upcoming(LocationRegistryStatus.preparing) is True
