"""Юнит-тесты чистой логики achievements_service (без БД)."""

from __future__ import annotations

from datetime import date, timedelta

from app.models import UserGoal
from app.services.achievements_service import (
    RunRow,
    _best_year_challenge,
    _best_year_level_dates,
    _calendar_days_challenge,
    _club_entry,
    _deja_vu_challenge,
    _first_letter,
    _goal_progress,
    _level_dates,
    _max_saturday_streak,
    _number_match_challenge,
    _p_index_challenge,
    _p_index_level_dates,
    _palindrome_challenge,
    _positions_challenge,
    _resolve_level,
    _runs_needed_for_p,
    _saturdays_left,
    _saturdays_of_year,
    _seconds_challenge,
    _start_numbers_range_challenge,
    _streak_level_dates,
    _threshold_dates,
    _time_display,
    _upcoming_hint,
    _weekdays_challenge,
    _year_fraction_elapsed,
)


def _row(
    event_date: date = date(2026, 1, 3),
    finish_time_sec: int | None = None,
    position: int | None = None,
    event_number: int | None = None,
    location_name: str = "Кузьминки",
    location_key: str = "kuzminki",
    region: str | None = "Москва",
    platform_code: str = "five_verst",
    is_pr: bool = False,
) -> RunRow:
    return RunRow(
        event_date=event_date,
        finish_time_sec=finish_time_sec,
        position=position,
        event_number=event_number,
        location_name=location_name,
        location_key=location_key,
        region=region,
        platform_code=platform_code,
        is_pr=is_pr,
    )


def test_resolve_level_progression() -> None:
    levels = {"bronze": 1, "silver": 3, "gold": 5}
    assert _resolve_level(0, levels) == (None, "bronze", 1)
    assert _resolve_level(1, levels) == ("bronze", "silver", 2)
    assert _resolve_level(4, levels) == ("silver", "gold", 1)
    assert _resolve_level(5, levels) == ("gold", None, None)
    assert _resolve_level(99, levels) == ("gold", None, None)


def test_time_display_short_format() -> None:
    assert _time_display(24 * 60 + 31) == "24:31"
    assert _time_display(25 * 60) == "25:00"
    assert _time_display(3600) == "01:00:00"


def test_seconds_challenge_counts_unique_seconds() -> None:
    rows = [
        _row(finish_time_sec=25 * 60 + 13),
        _row(finish_time_sec=26 * 60 + 13),  # секунда :13 уже закрыта
        _row(finish_time_sec=24 * 60 + 5),
        _row(finish_time_sec=None),
    ]
    challenge = _seconds_challenge(rows)
    assert challenge["current"] == 2
    cells = challenge["detail"]["cells"]  # type: ignore[index]
    assert cells[13]["done"] is True and cells[5]["done"] is True and cells[0]["done"] is False
    # тултип «где и когда впервые»: секунду :13 закрыла ПЕРВАЯ пробежка с ней
    assert cells[13]["date"] == "2026-01-03" and cells[13]["location"] == "Кузьминки"


def test_positions_challenge_uses_last_two_digits() -> None:
    rows = [_row(position=18), _row(position=118), _row(position=203), _row(position=None)]
    challenge = _positions_challenge(rows)
    assert challenge["current"] == 2  # 18 и 03 (118 дублирует 18)


def test_weekdays_challenge() -> None:
    rows = [
        _row(event_date=date(2026, 7, 4)),  # суббота
        _row(event_date=date(2026, 7, 5)),  # воскресенье
        _row(event_date=date(2026, 7, 11)),  # ещё суббота
    ]
    assert _weekdays_challenge(rows)["current"] == 2


def test_calendar_days_ignores_year() -> None:
    rows = [
        _row(event_date=date(2024, 3, 8)),
        _row(event_date=date(2026, 3, 8)),  # тот же день года
        _row(event_date=date(2026, 3, 9)),
    ]
    assert _calendar_days_challenge(rows)["current"] == 2


def test_palindrome_challenge_mirrors_minutes_seconds() -> None:
    rows = [
        _row(finish_time_sec=23 * 60 + 32),  # 23:32 — палиндром
        _row(finish_time_sec=21 * 60 + 12),  # 21:12 — палиндром
        _row(finish_time_sec=23 * 60 + 32),  # дубль не считается дважды
        _row(finish_time_sec=24 * 60 + 31),  # не палиндром
    ]
    challenge = _palindrome_challenge(rows)
    assert challenge["current"] == 2


def test_deja_vu_counts_repeated_times() -> None:
    rows = [
        _row(finish_time_sec=1500),
        _row(finish_time_sec=1500),
        _row(finish_time_sec=1501),
        _row(finish_time_sec=1502),
        _row(finish_time_sec=1502),
    ]
    assert _deja_vu_challenge(rows)["current"] == 2


def test_number_match_uses_chronological_run_index() -> None:
    rows = [
        _row(event_date=date(2026, 1, 3), event_number=5),
        _row(event_date=date(2026, 1, 10), event_number=2),  # 2-я пробежка на старте №2
        _row(event_date=date(2026, 1, 17), event_number=100),
    ]
    challenge = _number_match_challenge(rows)
    assert challenge["current"] == 1
    assert challenge["detail"]["items"][0]["value"] == "№2"  # type: ignore[index]


def test_p_index() -> None:
    rows = (
        [_row(location_key="a", location_name="А") for _ in range(3)]
        + [_row(location_key="b", location_name="Б") for _ in range(3)]
        + [_row(location_key="c", location_name="В") for _ in range(3)]
        + [_row(location_key="d", location_name="Г")]
    )
    assert _p_index_challenge(rows)["current"] == 3


def test_max_saturday_streak() -> None:
    dates = {
        date(2026, 6, 6),
        date(2026, 6, 13),
        date(2026, 6, 20),
        # пропуск 27 июня
        date(2026, 7, 4),
        date(2026, 7, 8),  # не суббота — не участвует
    }
    assert _max_saturday_streak(dates) == 3


def test_first_letter_rules() -> None:
    assert _first_letter("Кузьминки") == "К"
    assert _first_letter("Ёлочки") == "Е"
    assert _first_letter(" зелёный парк") == "З"
    assert _first_letter("5-й километр") is None


def test_saturdays_left_end_of_year() -> None:
    # 26.12.2026 — последняя суббота года; сегодняшняя суббота ещё считается
    assert _saturdays_left(date(2026, 12, 26)) == 1
    assert _saturdays_left(date(2026, 12, 20)) == 1
    assert _saturdays_left(date(2026, 12, 27)) == 0


def test_start_numbers_range_scoped_per_platform() -> None:
    # Номер старта — внутри своей системы: три закрытых номера на five_verst
    # и один на s95 не должны складываться в одну ячейку/счётчик.
    rows = [
        _row(event_number=50, platform_code="five_verst"),
        _row(event_number=51, platform_code="five_verst"),
        _row(event_number=52, platform_code="five_verst"),
        _row(event_number=60, platform_code="s95"),
    ]
    levels = {"bronze": 2, "silver": 3, "gold": 200}
    result = _start_numbers_range_challenge(
        rows, {}, code="start_numbers", title="Нумератор", description="", low=1, high=200, levels=levels
    )
    assert result["current"] == 3
    assert result["detail"]["platform_code"] == "five_verst"  # type: ignore[index]
    assert result["detail"]["cells"][49]["done"] is True  # type: ignore[index]
    assert result["detail"]["cells"][59]["done"] is False  # type: ignore[index]


def test_start_numbers_pro_range() -> None:
    rows = [_row(event_number=250, platform_code="s95"), _row(event_number=399, platform_code="s95")]
    levels = {"bronze": 50, "silver": 100, "gold": 200}
    pro = _start_numbers_range_challenge(
        rows, {}, code="start_numbers_pro", title="Нумератор ПРО", description="", low=201, high=400, levels=levels
    )
    assert pro["current"] == 2
    assert pro["detail"]["cells"][250 - 201]["done"] is True  # type: ignore[index]


def test_upcoming_hint_format() -> None:
    entries = [(date(2026, 7, 18), "Кузьминки"), (date(2026, 7, 25), "Люблино")]
    assert _upcoming_hint(entries) == "Скоро: Кузьминки ≈ 18.07, Люблино ≈ 25.07"
    assert _upcoming_hint(None) is None


def test_runs_needed_for_p() -> None:
    # p=3 при счётах [5, 4]: третьей локации нет — нужно 3 финиша в новой
    assert _runs_needed_for_p([5, 4], 3) == 3
    # p=3 при [3, 3, 2]: одной пробежки в третьей локации хватит
    assert _runs_needed_for_p([3, 3, 2], 3) == 1
    assert _runs_needed_for_p([10, 10, 10], 3) == 0


def _dates_seq(count: int, start: date = date(2018, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]


def test_club_entry_thresholds() -> None:
    entry = _club_entry("runs", "Пробежки", "🏃", _dates_seq(254))
    assert entry["earned"] == [10, 25, 50, 100, 250]
    assert entry["next_threshold"] == 500
    assert entry["to_next"] == 246
    assert entry["level_dates"]["10"] == (date(2018, 1, 1) + timedelta(days=9)).isoformat()
    assert entry["level_dates"]["250"] == (date(2018, 1, 1) + timedelta(days=249)).isoformat()
    assert entry["level_dates"]["500"] is None
    empty = _club_entry("runs", "Пробежки", "🏃", [])
    assert empty["earned"] == [] and empty["next_threshold"] == 10
    maxed = _club_entry("runs", "Пробежки", "🏃", _dates_seq(1500))
    assert maxed["next_threshold"] is None and maxed["pct_to_next"] == 100.0


def test_year_fraction_elapsed_bounds() -> None:
    assert 0 < _year_fraction_elapsed(date(2026, 1, 1)) < 0.01
    assert _year_fraction_elapsed(date(2026, 12, 31)) == 1.0


def test_best_year_challenge_takes_max_calendar_year() -> None:
    rows = (
        [_row(event_date=date(2024, 6, d)) for d in range(1, 11)]  # 10 в 2024
        + [_row(event_date=date(2025, 6, d)) for d in range(1, 26)]  # 25 в 2025
        + [_row(event_date=date(2026, 6, 5))]  # 1 в 2026
    )
    challenge = _best_year_challenge(rows)
    assert challenge["current"] == 25


def test_saturdays_of_year_count() -> None:
    # В 2026 году 1 января — четверг, 31 декабря — четверг: 52 полных субботы.
    assert len(_saturdays_of_year(2026)) == 52
    assert all(day.weekday() == 5 for day in _saturdays_of_year(2026))


def test_goal_progress_pr_count_year() -> None:
    goal = UserGoal(year=2026, goal_type="pr_count_year", target_value=3)
    rows = [
        _row(event_date=date(2026, 3, 1), is_pr=True),
        _row(event_date=date(2026, 4, 1), is_pr=False),
        _row(event_date=date(2026, 5, 1), is_pr=True),
        _row(event_date=date(2025, 1, 1), is_pr=True),  # прошлый год не считается
    ]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 7, 13))
    assert result["current_value"] == 2
    assert result["done"] is False


def test_goal_progress_saturday_consistency_year_on_track() -> None:
    goal = UserGoal(year=2026, goal_type="saturday_consistency_year", target_value=50)
    # На 10.01.2026 прошло ровно 2 субботы года (03.01 и 10.01) — обе активны, темп 100%.
    rows = [
        _row(event_date=date(2026, 1, 3)),
        _row(event_date=date(2026, 1, 10)),
    ]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 1, 10))
    assert result["current_value"] == 2
    assert result["current_display"] == "100%"
    assert result["target_display"] == "50%"


def test_goal_progress_saturday_consistency_year_not_achievable() -> None:
    goal = UserGoal(year=2026, goal_type="saturday_consistency_year", target_value=90)
    # Активна только в 1 из уже прошедших ~28 суббот года — 90% для оставшихся недостижимо.
    rows = [_row(event_date=date(2026, 1, 3))]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 7, 13))
    assert result["done"] is False
    assert result["on_track"] is False


def test_level_dates_generic_helper() -> None:
    levels = {"bronze": 2, "silver": 4, "gold": 6}
    dates = _dates_seq(5)  # только 5 событий — золото (6) недостижимо
    result = _level_dates(dates, levels)
    assert result["bronze"] == dates[1].isoformat()
    assert result["silver"] == dates[3].isoformat()
    assert result["gold"] is None


def test_threshold_dates_helper() -> None:
    dates = _dates_seq(30)
    result = _threshold_dates(dates, (10, 25, 50))
    assert result["10"] == dates[9].isoformat()
    assert result["25"] == dates[24].isoformat()
    assert result["50"] is None


def test_p_index_level_dates_matches_current() -> None:
    # 3 локации по 3 пробежки каждая — p-индекс должен стать 3 сразу после
    # третьего финиша в третьей локации (девятая пробежка по счёту).
    rows: list[RunRow] = []
    day = date(2020, 1, 1)
    for loc in ("a", "b", "c"):
        for _ in range(3):
            rows.append(_row(event_date=day, location_key=loc))
            day += timedelta(days=1)
    levels = {"bronze": 3, "silver": 5, "gold": 10}
    result = _p_index_level_dates(rows, levels)
    assert result["bronze"] == rows[8].event_date.isoformat()
    assert result["silver"] is None


def test_streak_level_dates_records_first_crossing() -> None:
    saturdays = {date(2026, 1, 3) + timedelta(days=7 * i) for i in range(5)}
    levels = {"bronze": 3, "silver": 5, "gold": 10}
    result = _streak_level_dates(saturdays, levels)
    sorted_saturdays = sorted(saturdays)
    assert result["bronze"] == sorted_saturdays[2].isoformat()
    assert result["silver"] == sorted_saturdays[4].isoformat()
    assert result["gold"] is None


def test_best_year_level_dates_earliest_year_wins() -> None:
    levels = {"bronze": 3, "silver": 5, "gold": 10}
    rows = (
        [_row(event_date=date(2024, 1, d)) for d in range(1, 4)]  # 2024: только 3
        + [_row(event_date=date(2025, 2, d)) for d in range(1, 6)]  # 2025: 5
    )
    result = _best_year_level_dates(rows, levels)
    assert result["bronze"] == date(2024, 1, 3).isoformat()
    assert result["silver"] == date(2025, 2, 5).isoformat()
    assert result["gold"] is None
