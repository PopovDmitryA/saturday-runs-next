from datetime import date
from uuid import uuid4

import pytest

from app.services.attendance_journal_service import (
    JOURNAL_METRICS,
    _journal_cache_key,
    _JournalEntry,
    _JournalRowDraft,
    _row_items,
    _volunteering_year_total,
    _year_bounds,
    get_attendance_journal,
)
from app.services.leaderboard_service import LEADERBOARD_METRICS
from app.services.location_page_service import (
    _attendance_month,
    _attendance_row_payload,
    _attendance_sort_key,
    _AttendancePerson,
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
        "locations:attendance:v2:meshchersky:2026:all:o0:l50"
    )


def test_location_attendance_cache_key_separates_months() -> None:
    # Без месяца ключ прежний — кэш годового журнала не сбрасывается.
    assert location_attendance_cache_key("meshchersky", 2026, "volunteers", 0, 50, None) == (
        "locations:attendance:v2:meshchersky:2026:volunteers:o0:l50"
    )
    assert location_attendance_cache_key("meshchersky", 2026, "volunteers", 0, 50, "2026-09") == (
        "locations:attendance:v2:meshchersky:2026:volunteers:m2026-09:o0:l50"
    )


@pytest.mark.parametrize(
    ("month", "year", "expected"),
    [
        ("2026-09", 2026, "2026-09"),
        ("2026-09", None, "2026-09"),
        ("2025-09", 2026, None),
        ("2026-13", 2026, None),
        ("2026-9", 2026, None),
        ("сентябрь", 2026, None),
        (None, 2026, None),
    ],
)
def test_attendance_month_accepts_only_months_of_the_year(month, year, expected) -> None:
    assert _attendance_month(month, year) == expected


def test_attendance_month_puts_month_volunteers_first() -> None:
    """Новичок с одним выходом в сентябре обгоняет ветеранов без сентября.

    06.10.2026: у Мещерского в сентябре волонтёрили 45 человек, а фильтр
    месяца показывал 25 — сервер резал порцию по годовому счёту, и Замира
    Ханбикова с единственным выходом 12.09 стояла 113-й.
    """
    veteran = _AttendancePerson(name="Анна СМИРНОВА")
    for day in (7, 14, 21, 28):
        veteran.vol_roles[date(2026, 2, day)] = {"Маршал"}
    newcomer = _AttendancePerson(name="Замира ХАНБИКОВА")
    newcomer.vol_roles[date(2026, 9, 12)] = {"Маршал"}
    runner = _AttendancePerson(name="Иван ИВАНОВ")
    runner.run_dates.add(date(2026, 9, 12))

    people = [veteran, runner, newcomer]
    by_year = sorted(people, key=lambda person: _attendance_sort_key(person, "volunteers"))
    assert by_year[0] is veteran

    by_month = sorted(people, key=lambda person: _attendance_sort_key(person, "volunteers", "2026-09"))
    # Пробежка в сентябре в волонтёрском срезе не считается — бегун ниже
    # ветерана, но из журнала не пропадает.
    assert by_month == [newcomer, veteran, runner]


def test_attendance_row_payload_hides_private_cells() -> None:
    person = _AttendancePerson(name="Иван ИВАНОВ", private=True)
    person.run_dates.add(date(2026, 2, 7))
    person.vol_roles[date(2026, 2, 14)] = {"Маршал"}

    payload = _attendance_row_payload(person)
    assert payload["year_total"] == 2
    assert payload["items"] == []
    # Счёт по месяцам отдаём и закрытому профилю: колонка «Всего» в срезе
    # месяца должна быть заполнена у всех (Дмитрий 03.09.2026). Это счёт, а не
    # даты — клетки такому человеку по-прежнему не рисуются.
    assert payload["month_totals"] == {"2026-02": 2}

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


def test_attendance_kind_is_a_slice_not_a_row_filter() -> None:
    """«Бегуны»/«Волонтёры» режут САМИ КЛЕТКИ, а не только набор строк.

    До 28.08.2026 фильтр лишь выбрасывал тех, у кого нет пробежек (или
    волонтёрств), а клетки оставались прежними — у человека с обеими ролями
    переключатель не менял ровным счётом ничего.
    """
    person = _AttendancePerson(name="Иван ИВАНОВ")
    person.run_dates.add(date(2026, 2, 7))
    person.run_dates.add(date(2026, 2, 14))
    person.vol_roles[date(2026, 2, 7)] = {"Маршал"}
    person.vol_roles[date(2026, 2, 21)] = {"Фотограф"}

    runners = _attendance_row_payload(person, kind="runners")
    assert runners["year_total"] == 2
    assert [item["date"] for item in runners["items"]] == ["2026-02-14", "2026-02-07"]
    # В беговом срезе роли не показываются даже там, где они были.
    assert all(item["roles"] == [] for item in runners["items"])

    volunteers = _attendance_row_payload(person, kind="volunteers")
    assert volunteers["year_total"] == 2
    assert [item["date"] for item in volunteers["items"]] == ["2026-02-21", "2026-02-07"]
    assert all(item["run"] is False for item in volunteers["items"])

    # «Все» — дни обеих ролей, день с пробежкой и волонтёрством считается один раз.
    both = _attendance_row_payload(person, kind="all")
    assert both["year_total"] == 3
    day_with_both = next(item for item in both["items"] if item["date"] == "2026-02-07")
    assert day_with_both["run"] is True
    assert day_with_both["roles"] == ["Маршал"]
