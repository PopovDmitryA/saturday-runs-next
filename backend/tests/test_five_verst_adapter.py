from __future__ import annotations

from datetime import date

import pytest

from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.platform_adapters.five_verst.parser import (
    dedupe_profile_runs,
    dedupe_profile_volunteering,
    parse_userstats_html,
)
from app.platform_adapters.five_verst.url import InvalidProfileUrlError, normalize_profile_url, parse_profile_input

SAMPLE_HTML = """
<html>
<head><title>Статистика участника Дмитрий ПОПОВ | 5 вёрст</title></head>
<body>
<h1>Статистика участника Дмитрий ПОПОВ</h1>
<div class="stats">
  <span>Всего финишей</span><strong>148</strong>
  <span>Всего волонтёрств</span><strong>46</strong>
</div>
</body>
</html>
"""


def test_normalize_profile_url_accepts_canonical_form() -> None:
    parsed = normalize_profile_url("https://5verst.ru/userstats/12345/")
    assert parsed.user_id == "12345"
    assert parsed.canonical_url == "https://5verst.ru/userstats/12345/"


def test_normalize_profile_url_accepts_without_scheme() -> None:
    parsed = normalize_profile_url("5verst.ru/userstats/99/")
    assert parsed.user_id == "99"


def test_normalize_profile_url_rejects_invalid() -> None:
    with pytest.raises(InvalidProfileUrlError):
        normalize_profile_url("https://example.com/profile/1")


def test_parse_profile_input_accepts_numeric_code() -> None:
    parsed = parse_profile_input("790096427")
    assert parsed.user_id == "790096427"
    assert parsed.canonical_url == "https://5verst.ru/userstats/790096427/"


def test_parse_profile_input_accepts_barcode_with_latin_a() -> None:
    parsed = parse_profile_input("A790096427")
    assert parsed.user_id == "790096427"


def test_parse_profile_input_accepts_barcode_with_cyrillic_a() -> None:
    parsed = parse_profile_input("А790096427")
    assert parsed.user_id == "790096427"


def test_parse_profile_input_rejects_garbage() -> None:
    with pytest.raises(InvalidProfileUrlError):
        parse_profile_input("not-a-profile")


def test_parse_userstats_html_extracts_participant() -> None:
    parsed_url = normalize_profile_url("https://5verst.ru/userstats/12345/")
    participant = parse_userstats_html(SAMPLE_HTML, parsed_url)
    assert participant.external_user_id == "12345"
    assert participant.display_name == "Дмитрий ПОПОВ"
    assert participant.total_runs == 148
    assert participant.total_volunteering == 46


RUNS_TABLE_HTML = """
<table>
<tr><th>Дата</th><th>Мероприятие</th><th>Время</th></tr>
<tr>
  <td>09.05.2026</td>
  <td><a href="https://5verst.ru/pervouralsknaberezhnaya/results/09.05.2026">Первоуральск #94</a></td>
  <td>00:23:42</td>
</tr>
</table>
"""

VOLS_TABLE_HTML = """
<table>
<tr><th>Дата</th><th>Мероприятие</th><th>Роль</th></tr>
<tr>
  <td>23.05.2026</td>
  <td><a href="https://5verst.ru/meshchersky/results/23.05.2026">Мещерский #213</a></td>
  <td>Составление отчёта</td>
</tr>
</table>
"""


def test_parse_userstats_runs_html() -> None:
    from app.platform_adapters.five_verst.parser import parse_userstats_runs_html

    runs = parse_userstats_runs_html(RUNS_TABLE_HTML, "790103773", "Дмитрий ПОПОВ")
    assert len(runs) == 1
    assert runs[0].external_user_id == "790103773"
    assert runs[0].location_external_key == "pervouralsknaberezhnaya"
    assert runs[0].location_name == "Первоуральск"
    assert runs[0].event_number == 94
    assert runs[0].finish_time_display == "00:23:42"
    assert runs[0].external_result_key == "790103773:2026-05-09:pervouralsknaberezhnaya"


def test_dedupe_profile_runs_same_day_same_location() -> None:
    items = [
        CanonicalRunResult(
            external_result_key="790103773:2026-04-04:meshchersky:old",
            event_date=date(2026, 4, 4),
            external_user_id="790103773",
            participant_name="Test",
            position=None,
            finish_time_sec=1500,
            finish_time_display="00:25:00",
            location_external_key="meshchersky",
            location_name="Мещерский",
        ),
        CanonicalRunResult(
            external_result_key="790103773:meshchersky:99:2026-04-04",
            event_date=date(2026, 4, 4),
            external_user_id="790103773",
            participant_name="Test",
            position=None,
            finish_time_sec=None,
            finish_time_display=None,
            location_external_key="meshchersky",
            location_name="Мещерский",
        ),
    ]
    deduped = dedupe_profile_runs(items, "790103773")
    assert len(deduped) == 1
    assert deduped[0].external_result_key == "790103773:2026-04-04:meshchersky"
    assert deduped[0].finish_time_sec == 1500


def test_parse_userstats_volunteering_html() -> None:
    from app.platform_adapters.five_verst.parser import parse_userstats_volunteering_html

    items = parse_userstats_volunteering_html(VOLS_TABLE_HTML, "790103773", "Дмитрий ПОПОВ")
    assert len(items) == 1
    assert items[0].role == "Составление отчёта"
    assert items[0].location_external_key == "meshchersky"
    assert items[0].external_result_key == "790103773:2026-05-23:meshchersky:составление_отчёта"


def test_dedupe_profile_volunteering_same_day_same_location() -> None:
    items = [
        _vol("790103773", date(2026, 4, 4), "meshchersky", "Роль A"),
        _vol("790103773", date(2026, 4, 4), "meshchersky", "Роль B"),
    ]
    deduped = dedupe_profile_volunteering(items, "790103773")
    assert len(deduped) == 2
    assert {item.role for item in deduped} == {"Роль A", "Роль B"}
    assert deduped[0].external_result_key == "790103773:2026-04-04:meshchersky:роль_a"
    assert deduped[1].external_result_key == "790103773:2026-04-04:meshchersky:роль_b"


def test_dedupe_profile_volunteering_same_day_different_locations() -> None:
    items = [
        _vol("790103773", date(2025, 10, 11), "petergofaleksandriysky", "Роль A"),
        _vol("790103773", date(2025, 10, 11), "mikhalkovo", "Роль B"),
    ]
    deduped = dedupe_profile_volunteering(items, "790103773")
    assert len(deduped) == 2
    assert {item.location_external_key for item in deduped} == {
        "petergofaleksandriysky",
        "mikhalkovo",
    }


def test_dedupe_profile_volunteering_keeps_multiple_roles_on_same_day() -> None:
    items = [
        _vol("790103773", date(2026, 4, 4), "meshchersky", "Составление отчёта"),
        _vol("790103773", date(2026, 4, 4), "meshchersky", "Организатор"),
        _vol("790103773", date(2026, 4, 4), "meshchersky", "Обработка результатов"),
    ]
    deduped = dedupe_profile_volunteering(items, "790103773")
    assert len(deduped) == 3
    assert {item.role for item in deduped} == {
        "Составление отчёта",
        "Организатор",
        "Обработка результатов",
    }


def test_dedupe_profile_volunteering_new_years_day_keeps_roles_per_location() -> None:
    items = [
        _vol("790103773", date(2026, 1, 1), "meshchersky", "Составление отчёта"),
        _vol("790103773", date(2026, 1, 1), "sokolniki", "Организатор"),
        _vol("790103773", date(2026, 1, 1), "meshchersky", "Инструктаж"),
    ]
    deduped = dedupe_profile_volunteering(items, "790103773")
    assert len(deduped) == 3
    assert {item.external_result_key for item in deduped} == {
        "790103773:2026-01-01:meshchersky:составление_отчёта",
        "790103773:2026-01-01:sokolniki:организатор",
        "790103773:2026-01-01:meshchersky:инструктаж",
    }


def _vol(user_id: str, event_date: date, slug: str, role: str) -> CanonicalVolunteerResult:
    return CanonicalVolunteerResult(
        external_result_key=f"{user_id}:tmp",
        event_date=event_date,
        external_user_id=user_id,
        participant_name="Test",
        role=role,
        location_external_key=slug,
        location_name=slug,
    )
