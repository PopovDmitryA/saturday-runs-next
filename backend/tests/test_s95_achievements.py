from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from app.s95.parsers.achievements import parse_event_stats_html, parse_row_achievements
from app.s95.parsers.protocol import parse_protocol_page

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "s95_protocol.html"


def test_parse_event_stats_fixture() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    stats = parse_event_stats_html(html)
    assert stats.finishers_count == 70
    assert stats.personal_records_count == 16
    assert stats.first_at_location_count == 5
    assert stats.first_at_s95_count == 6
    assert stats.volunteers_count == 28


def test_parse_row_achievements_from_fixture_rows() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find("table").find_all("tr")[1:]
    pr = parse_row_achievements(rows[0])
    first_s95 = parse_row_achievements(rows[1])
    first_loc = parse_row_achievements(rows[2])
    assert pr.is_pr is True
    assert first_s95.is_first_run is True
    assert first_s95.is_first_run_at_location is False
    assert first_loc.is_first_run_at_location is True
    assert first_loc.is_first_run is False


def test_parse_protocol_page_fixture() -> None:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    parsed = parse_protocol_page(
        html,
        "https://s95.ru/activities/4098",
        location_external_key="gorky",
        location_name="Парк Горького",
        event_date=__import__("datetime").date(2025, 4, 12),
        event_number=4098,
    )
    assert parsed.event_stats.personal_records_count == 16
    assert len(parsed.run_results) == 3
    assert parsed.run_results[0].is_pr is True
    assert parsed.run_results[0].club_name == "Ангарка"
    assert parsed.run_results[1].is_first_run is True
    assert parsed.run_results[2].is_first_run_at_location is True
