from __future__ import annotations

from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from app.s95.parkrun import is_parkrun_eligible_barcode, normalize_parkrun_athlete_id
from app.s95.parsers.athlete import (
    enrich_participant_activity_totals,
    parse_athlete_html,
    parse_athlete_runs_html,
    parse_athlete_volunteering_html,
    parse_barcode_and_planning,
)
from app.s95.parsers.protocol import parse_protocol_html
from app.s95.parsers.summary import parse_location_summary_html
from app.platform_adapters.s95.url import parse_athlete_url

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_athlete_url() -> None:
    parsed = parse_athlete_url("https://s95.ru/athletes/5207/")
    assert parsed.external_user_id == "5207"
    assert parsed.canonical_url == "https://s95.ru/athletes/5207/"


def test_enrich_totals_from_results_table_without_legacy_labels() -> None:
    html = (FIXTURES / "s95_athlete.html").read_text(encoding="utf-8")
    html = html.replace("<p>Всего финишей 42</p>", "").replace("<p>Всего волонтёрств 3</p>", "")
    profile = parse_athlete_html(html, "https://s95.ru/athletes/5207/", "5207")
    assert profile.total_runs is None
    enriched = enrich_participant_activity_totals(html, profile)
    assert enriched.total_runs == 1
    assert enriched.total_volunteering == 1


def test_parse_athlete_profile_fixture() -> None:
    html = (FIXTURES / "s95_athlete.html").read_text(encoding="utf-8")
    profile = parse_athlete_html(html, "https://s95.ru/athletes/5207/", "5207")
    assert profile.display_name == "Иван Иванов"
    assert profile.barcode_id == "A7035519"
    assert profile.planning_location == "Парк Горького"
    assert profile.total_runs == 42

    barcode, planning = parse_barcode_and_planning(BeautifulSoup(html, "html.parser"))
    assert barcode == "A7035519"
    assert planning == "Парк Горького"


def test_parse_athlete_runs_modern_table_columns() -> None:
    html = """
    <table>
      <tr><th>#</th><th>Дата</th><th>Время</th><th>Темп</th><th>Место</th><th>Мероприятие</th></tr>
      <tr><td>12</td><td>23.05.2026</td><td>23:41</td><td>4:44</td><td>1</td><td><a href="/events/troitsk">Троицк</a></td></tr>
    </table>
    """
    runs = parse_athlete_runs_html(
        html,
        external_user_id="5207",
        display_name="Test",
        profile_url="https://s95.ru/athletes/5207/",
    )
    assert len(runs) == 1
    assert runs[0].event_number == 12
    assert runs[0].position == 1
    assert runs[0].finish_time_display == "00:23:41"
    assert runs[0].pace_display == "4:44"
    assert runs[0].pace_sec_per_km == 4 * 60 + 44
    assert runs[0].location_name == "Троицк"
    assert runs[0].external_result_key == "profile:5207:2026-05-23:troitsk"


def test_parse_athlete_runs_and_volunteering_fixture() -> None:
    html = (FIXTURES / "s95_athlete.html").read_text(encoding="utf-8")
    runs = parse_athlete_runs_html(
        html,
        external_user_id="5207",
        display_name="Иван Иванов",
        profile_url="https://s95.ru/athletes/5207/",
    )
    assert len(runs) == 1
    assert runs[0].event_date == date(2025, 4, 12)
    assert runs[0].finish_time_display == "00:23:42"

    vols = parse_athlete_volunteering_html(
        html,
        external_user_id="5207",
        display_name="Иван Иванов",
        profile_url="https://s95.ru/athletes/5207/",
    )
    assert len(vols) == 1
    assert vols[0].role == "Маршал"
    assert vols[0].external_result_key == "vol:парк_горького:2025-01-05:5207:Маршал"


def test_parse_athlete_volunteering_accordion_fixture() -> None:
    html = (FIXTURES / "s95_athlete_volunteering_accordion.html").read_text(encoding="utf-8")
    vols = parse_athlete_volunteering_html(
        html,
        external_user_id="7054",
        display_name="Серафим СОКОЛОВ",
        profile_url="https://s95.ru/athletes/7054/",
    )
    assert len(vols) == 3
    roles = {item.role for item in vols}
    assert roles == {"Замыкающий", "Разметка трассы"}
    assert all(item.external_result_key.startswith("vol:troitsk:") for item in vols)
    assert all(item.external_user_id == "7054" for item in vols)


def test_parse_summary_fixture() -> None:
    html = (FIXTURES / "s95_summary.html").read_text(encoding="utf-8")
    summaries = parse_location_summary_html(
        html,
        "https://s95.ru/locations/gorky/",
        location_external_key="gorky",
        location_name="Парк Горького",
    )
    assert len(summaries) == 1
    assert summaries[0].event_date == date(2025, 4, 12)
    assert summaries[0].finishers_count == 150
    assert summaries[0].best_male_time_display == "00:18:59"


def test_parse_protocol_fixture() -> None:
    html = (FIXTURES / "s95_protocol.html").read_text(encoding="utf-8")
    runs, vols = parse_protocol_html(
        html,
        "https://s95.ru/events/100/protocol/",
        location_external_key="gorky",
        location_name="Парк Горького",
        event_date=date(2025, 4, 12),
        event_number=100,
    )
    assert len(runs) == 3
    assert runs[0].external_user_id == "5207"
    assert runs[0].finish_time_display == "00:23:42"
    from app.pace import resolve_run_pace

    pace_sec, pace_display = resolve_run_pace(
        runs[0].pace_sec_per_km,
        runs[0].pace_display,
        runs[0].finish_time_sec,
    )
    assert pace_display == "4:44"
    assert pace_sec == 284
    assert runs[0].is_pr is True
    assert len(vols) == 1
    assert vols[0].external_user_id == "5207"


def test_parkrun_barcode_rules() -> None:
    assert is_parkrun_eligible_barcode("A7035519")
    assert not is_parkrun_eligible_barcode("A770005892")
    assert normalize_parkrun_athlete_id("A7035519") == "7035519"
    assert normalize_parkrun_athlete_id("А7035519") == "7035519"
    assert normalize_parkrun_athlete_id("7035519") == "7035519"
