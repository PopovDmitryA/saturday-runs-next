from datetime import date
from uuid import uuid4

import pytest

from app.services.attendance_journal_service import (
    JOURNAL_METRICS,
    _JournalEntry,
    _JournalRowDraft,
    _journal_cache_key,
    _row_items,
    _volunteering_year_total,
    _year_bounds,
    get_attendance_journal,
)
from app.services.leaderboard_service import LEADERBOARD_METRICS
from app.services.location_page_service import (
    _AttendancePerson,
    _attendance_row_payload,
    location_attendance_cache_key,
)


def test_journal_metrics_are_known_leaderboards() -> None:
    for metric in JOURNAL_METRICS:
        assert metric in LEADERBOARD_METRICS


def test_year_bounds() -> None:
    assert _year_bounds(2026) == (date(2026, 1, 1), date(2027, 1, 1))


def test_journal_cache_key_distinguishes_variants() -> None:
    keys = {
        _journal_cache_key("runs", 2026, "all", 0, 50),
        _journal_cache_key("runs", 2025, "all", 0, 50),
        _journal_cache_key("runs", 2026, "s95", 0, 50),
        _journal_cache_key("runs", 2026, "all", 50, 50),
        _journal_cache_key("volunteering", 2026, "all", 0, 50),
    }
    assert len(keys) == 5


def test_unknown_metric_has_no_journal() -> None:
    with pytest.raises(ValueError):
        get_attendance_journal(None, "wins")  # type: ignore[arg-type]


def test_volunteering_year_total_occasions() -> None:
    loc_a, loc_b = uuid4(), uuid4()
    entries = [
        # Обычный день 5 вёрст: две записи в один день — один зачёт.
        _JournalEntry(date(2026, 3, 7), loc_a, "five_verst", "Маршал"),
        _JournalEntry(date(2026, 3, 7), loc_a, "five_verst", "Фотограф"),
        # 1 января: две РАЗНЫЕ локации — два зачёта (инвентаризация).
        _JournalEntry(date(2026, 1, 1), loc_a, "five_verst"),
        _JournalEntry(date(2026, 1, 1), loc_b, "five_verst"),
        # S95: каждая запись считается отдельно, даже в один день.
        _JournalEntry(date(2026, 3, 7), loc_a, "s95"),
        _JournalEntry(date(2026, 3, 7), loc_a, "s95"),
    ]
    assert _volunteering_year_total(entries) == 5


def test_row_items_tourism_marks_new_and_counts_only_new() -> None:
    loc = uuid4()
    row_key = "abc123"
    identity_by_location = {loc: "meshchersky"}
    identity_names = {"meshchersky": "Мещерский"}
    identity_slugs = {"meshchersky": "meshchersky"}
    draft = _JournalRowDraft(
        entries=[
            _JournalEntry(date(2026, 2, 7), loc, "five_verst"),
            _JournalEntry(date(2026, 3, 7), loc, "five_verst"),
        ]
    )
    first_visits = {(row_key, "meshchersky"): date(2026, 2, 7)}
    items, year_total = _row_items(
        "locations",
        row_key,
        draft,
        identity_by_location,
        identity_names,
        identity_slugs,
        first_visits,
    )
    # «Всего» журнала туризма — только новые площадки года.
    assert year_total == 1
    by_date = {item["date"]: item for item in items}
    assert by_date["2026-02-07"]["new"] is True
    assert by_date["2026-03-07"]["new"] is False
    assert by_date["2026-02-07"]["location"] == "Мещерский"
    assert by_date["2026-02-07"]["slug"] == "meshchersky"


def test_row_items_runs_dedupes_double_rows() -> None:
    loc = uuid4()
    draft = _JournalRowDraft(
        entries=[
            _JournalEntry(date(2026, 2, 7), loc, "five_verst"),
            _JournalEntry(date(2026, 2, 7), loc, "five_verst"),
        ]
    )
    items, year_total = _row_items(
        "runs", "k", draft, {loc: "x"}, {"x": "X"}, {}, {}
    )
    assert len(items) == 1
    assert year_total == 1


def test_row_items_volunteering_carries_role_label() -> None:
    loc = uuid4()
    draft = _JournalRowDraft(entries=[_JournalEntry(date(2026, 2, 7), loc, "s95", "Маршал")])
    items, year_total = _row_items(
        "volunteering", "k", draft, {loc: "x"}, {"x": "X"}, {}, {}
    )
    assert year_total == 1
    assert items[0]["role"] == "Маршал"


def test_location_attendance_cache_key_normalizes_slug() -> None:
    assert location_attendance_cache_key(" Meshchersky ", 2026, "all", 0, 50) == (
        "locations:attendance:v1:meshchersky:2026:all:o0:l50"
    )


def test_attendance_row_payload_hides_private_cells() -> None:
    person = _AttendancePerson(name="Иван ИВАНОВ", private=True)
    person.run_dates.add(date(2026, 2, 7))
    person.vol_roles[date(2026, 2, 14)] = {"Маршал"}

    payload = _attendance_row_payload(person)
    assert payload["year_total"] == 2
    assert payload["items"] == []

    # Свою строку человек видит целиком, даже с закрытым профилем.
    own = _attendance_row_payload(person, me=True)
    assert len(own["items"]) == 2


def test_attendance_row_payload_marks_run_and_roles() -> None:
    person = _AttendancePerson(name="Иван ИВАНОВ")
    person.run_dates.add(date(2026, 2, 7))
    person.vol_roles[date(2026, 2, 7)] = {"Фотограф"}
    payload = _attendance_row_payload(person)
    assert payload["year_total"] == 1
    item = payload["items"][0]
    assert item["run"] is True
    assert item["roles"] == ["Фотограф"]
