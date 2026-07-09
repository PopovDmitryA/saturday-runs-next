from __future__ import annotations

from pathlib import Path

from app.platform_adapters.five_verst import clubs_parser
from app.sync.five_verst_clubs import normalize_club_name

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_clubs_list_fixture() -> None:
    entries = clubs_parser.parse_clubs_list_html(_read("five_verst_clubs_list.html"))
    assert len(entries) == 4
    by_slug = {entry.slug: entry for entry in entries}

    myruno = by_slug["myruno"]
    assert myruno.name == "MyRUNo"
    assert myruno.source_url == "https://5verst.ru/clubs/myruno/"
    assert myruno.members_count == 84
    assert myruno.finishes_count == 3979
    assert myruno.volunteerings_count == 1943
    assert myruno.avg_time_seconds == 26 * 60 + 43
    assert myruno.best_female_time_seconds == 19 * 60 + 51
    assert myruno.best_male_time_seconds == 15 * 60 + 32
    assert {link["kind"] for link in myruno.external_links} == {"site", "vk", "telegram"}

    # Club with no runs yet: zeros and empty times.
    loviweter = by_slug["loviweter"]
    assert loviweter.name == "#LoviWeter"
    assert loviweter.members_count == 0
    assert loviweter.avg_time_seconds is None
    assert loviweter.external_links == []


def test_clubs_list_row_hash_reacts_to_changes() -> None:
    html = _read("five_verst_clubs_list.html")
    first = clubs_parser.parse_clubs_list_html(html)
    second = clubs_parser.parse_clubs_list_html(html)
    assert [e.row_hash for e in first] == [e.row_hash for e in second]

    # Change myruno's finishes count (3979 → 4001) everywhere it appears in the row.
    changed = clubs_parser.parse_clubs_list_html(html.replace("3979", "4001"))
    by_slug_before = {e.slug: e.row_hash for e in first}
    by_slug_after = {e.slug: e.row_hash for e in changed}
    assert by_slug_before["myruno"] != by_slug_after["myruno"]
    assert by_slug_before["loviweter"] == by_slug_after["loviweter"]


def test_parse_club_detail_fixture() -> None:
    detail = clubs_parser.parse_club_detail_html(_read("five_verst_club_detail.html"), "5-vyorst-turisty")
    assert detail.slug == "5-vyorst-turisty"
    assert detail.title == "5 вёрст-туристы"
    assert detail.description is not None
    assert "путешествовать" in detail.description
    assert detail.logo_url is None
    assert detail.members_skipped == 0
    assert len(detail.members) == 4
    first = detail.members[0]
    assert first.userstats_id == "790057093"
    assert first.display_name == "Наталия ТУМКОВСКАЯ"
    assert first.profile_url == "https://5verst.ru/userstats/790057093/"


def test_parse_club_detail_logo_and_links() -> None:
    detail = clubs_parser.parse_club_detail_html(_read("five_verst_club_detail_logo.html"), "myruno")
    assert detail.title == "Спортивное сообщество MyRUNo (Марьино)"
    assert detail.logo_url == "https://storage.yandexcloud.net/event-pictures-prod/club_logos/myruno.png"
    assert {link["kind"] for link in detail.external_links} == {"site", "vk", "telegram"}
    assert len(detail.members) == 2


def test_normalize_club_name() -> None:
    assert normalize_club_name("  MyRUNo ") == "myruno"
    assert normalize_club_name("#ПоБеги") == "побеги"
    assert normalize_club_name("Бегущие  Ёжики") == "бегущие ежики"
