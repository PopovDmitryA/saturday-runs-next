"""Юнит-тесты чистой логики achievements_service (без БД)."""

from __future__ import annotations

from datetime import date, timedelta

from app.models import UserGoal
from app.services.achievements_service import (
    REVIEW_MIN_COMMENT_LEN,
    RatingRow,
    RunRow,
    _best_year_challenge,
    _best_year_level_dates,
    _calendar_days_challenge,
    _club_entry,
    _compute_clubs,
    _deja_vu_challenge,
    _first_letter,
    _goal_progress,
    _inspector_challenge,
    _level_dates,
    _max_saturday_streak,
    _number_match_challenge,
    _p_index_challenge,
    _p_index_level_dates,
    _palindrome_challenge,
    _positions_challenge,
    _resolve_level,
    _reviewer_challenge,
    _rows_before_last_activity,
    _runs_needed_for_p,
    _saturdays_left,
    _saturdays_of_year,
    _scope_by_platform,
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


def _rating_row(
    rated_on: date = date(2026, 1, 4),
    platform_code: str = "five_verst",
    is_review: bool = False,
) -> RatingRow:
    return RatingRow(rated_on=rated_on, platform_code=platform_code, is_review=is_review)


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
        _row(event_date=date(2024, 3, 8), platform_code="five_verst"),
        _row(event_date=date(2026, 3, 8), platform_code="s95"),  # тот же день года
        _row(event_date=date(2026, 3, 9), platform_code="s95"),
    ]
    result = _calendar_days_challenge(rows)
    assert result["current"] == 2
    days = result["detail"]["days"]  # type: ignore[index]
    assert days[0]["platform_code"] == "five_verst"
    assert days[1]["platform_code"] == "s95"


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


def test_deja_vu_items_not_truncated_past_twenty() -> None:
    """Раньше detail.items резался на первых 20 — активный участник с 20+
    совпадениями не видел часть строк вообще (и без скролла на фронте не
    поместились бы). current должен совпадать с полным числом найденных."""
    rows = []
    for sec in range(1000, 1030):
        rows.append(_row(finish_time_sec=sec))
        rows.append(_row(finish_time_sec=sec))
    challenge = _deja_vu_challenge(rows)
    assert challenge["current"] == 30
    assert len(challenge["detail"]["items"]) == 30  # type: ignore[arg-type]


def test_scope_by_platform_filters_rows_vol_rows_and_upcoming() -> None:
    rows = [
        _row(platform_code="five_verst", event_date=date(2026, 1, 3)),
        _row(platform_code="s95", event_date=date(2026, 1, 10)),
    ]
    vol_rows = {
        "five_verst": [(date(2026, 1, 3), "kuzminki")],
        "s95": [(date(2026, 1, 10), "sokolniki")],
    }
    upcoming = {
        ("five_verst", 42): [(date(2026, 1, 17), "Кузьминки")],
        ("s95", 42): [(date(2026, 1, 17), "Сокольники")],
    }
    rating_rows = [
        _rating_row(platform_code="five_verst", rated_on=date(2026, 1, 4)),
        _rating_row(platform_code="s95", rated_on=date(2026, 1, 11)),
    ]

    scoped_rows, scoped_vol_rows, scoped_upcoming, scoped_rating_rows = _scope_by_platform(
        rows, vol_rows, upcoming, rating_rows, "s95"
    )
    assert [row.platform_code for row in scoped_rows] == ["s95"]
    assert set(scoped_vol_rows) == {"s95"}
    assert set(scoped_upcoming) == {("s95", 42)}
    assert [row.platform_code for row in scoped_rating_rows] == ["s95"]

    assert _scope_by_platform(rows, vol_rows, upcoming, rating_rows, None) == (
        rows,
        vol_rows,
        upcoming,
        rating_rows,
    )


def test_inspector_counts_every_rating() -> None:
    rows = [_rating_row(rated_on=date(2026, 1, 4) + timedelta(days=i)) for i in range(25)]
    challenge = _inspector_challenge(rows)
    assert challenge["code"] == "inspector"
    assert challenge["current"] == 25
    # Бронза — на 25-й оценке, то есть в день последней из них.
    assert challenge["level"] == "bronze"
    assert challenge["level_dates"]["bronze"] == (date(2026, 1, 4) + timedelta(days=24)).isoformat()  # type: ignore[index]


def test_inspector_empty_has_no_level() -> None:
    challenge = _inspector_challenge([])
    assert challenge["current"] == 0
    assert challenge["level"] is None
    assert challenge["level_dates"] == {"bronze": None, "silver": None, "gold": None}


def test_reviewer_counts_only_reviews() -> None:
    rows = [
        *[_rating_row(rated_on=date(2026, 1, 4), is_review=False) for _ in range(12)],
        *[_rating_row(rated_on=date(2026, 1, 5), is_review=True) for _ in range(10)],
    ]
    challenge = _reviewer_challenge(rows)
    assert challenge["code"] == "reviewer"
    # Голые звёзды в рецензии не идут — только 10 развёрнутых.
    assert challenge["current"] == 10
    assert challenge["level"] == "bronze"


def test_reviewer_and_inspector_count_same_rating_once_each() -> None:
    # Одна оценка с текстом двигает оба счётчика: Ревизор считает все оценки,
    # Рецензент — только развёрнутые.
    rows = [_rating_row(is_review=True)]
    assert _inspector_challenge(rows)["current"] == 1
    assert _reviewer_challenge(rows)["current"] == 1


def test_review_min_comment_len_matches_product_rule() -> None:
    # Порог рецензии зафиксирован в посте и в тексте достижения.
    assert REVIEW_MIN_COMMENT_LEN == 50


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


def test_start_numbers_range_counts_any_platform() -> None:
    # Число засчитывается, если получено В ЛЮБОЙ системе: три закрытых номера
    # на five_verst и один на s95 складываются в общий счётчик диапазона.
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
    assert result["current"] == 4
    assert result["detail"]["cells"][49]["done"] is True  # type: ignore[index]
    assert result["detail"]["cells"][49]["platform_code"] == "five_verst"  # type: ignore[index]
    assert result["detail"]["cells"][59]["done"] is True  # type: ignore[index]
    assert result["detail"]["cells"][59]["platform_code"] == "s95"  # type: ignore[index]


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


def test_club_entry_extra_count_adds_undated_credits() -> None:
    """parkrun отдаёт только общий счётчик волонтёрств без дат — extra_count
    поднимает current/earned, но не может дать level_dates за пределами
    того, что реально известно по датам."""
    entry = _club_entry("volunteering", "Волонтёрства", "💚", _dates_seq(5), extra_count=95)
    assert entry["current"] == 100
    assert entry["earned"] == [10, 25, 50, 100]
    assert entry["level_dates"]["10"] is None
    assert entry["level_dates"]["100"] is None
    plain = _club_entry("volunteering", "Волонтёрства", "💚", _dates_seq(5))
    assert plain["current"] == 5


def test_compute_clubs_folds_in_parkrun_volunteer_total() -> None:
    rows = [_row(platform_code="parkrun", event_date=date(2026, 1, 3))]
    clubs = _compute_clubs(rows, {}, parkrun_volunteer_total=171)

    overall_vol = next(e for e in clubs["overall"] if e["code"] == "volunteering")
    assert overall_vol["current"] == 171
    assert overall_vol["earned"] == [10, 25, 50, 100]

    parkrun_platform = next(p for p in clubs["platforms"] if p["platform_code"] == "parkrun")
    parkrun_vol = next(e for e in parkrun_platform["entries"] if e["code"] == "volunteering")
    assert parkrun_vol["current"] == 171


def test_compute_clubs_parkrun_platform_appears_without_runs() -> None:
    """Волонтёр parkrun без единой пробежки всё равно должен попасть в
    список платформенных клубов."""
    clubs = _compute_clubs([], {}, parkrun_volunteer_total=15)
    codes = {platform["platform_code"] for platform in clubs["platforms"]}
    assert "parkrun" in codes


def test_compute_clubs_no_parkrun_total_omits_platform() -> None:
    clubs = _compute_clubs([], {}, parkrun_volunteer_total=0)
    codes = {platform["platform_code"] for platform in clubs["platforms"]}
    assert "parkrun" not in codes


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


def test_rows_before_last_activity_empty() -> None:
    assert _rows_before_last_activity([]) is None


def test_rows_before_last_activity_drops_last_day_only() -> None:
    rows = [
        _row(event_date=date(2026, 1, 3)),
        _row(event_date=date(2026, 1, 10)),
        _row(event_date=date(2026, 1, 17)),
    ]
    before = _rows_before_last_activity(rows)
    assert before is not None
    assert [row.event_date for row in before] == [date(2026, 1, 3), date(2026, 1, 10)]


def test_rows_before_last_activity_drops_whole_last_day_even_if_multiple_platforms() -> None:
    # Два старта в один день на разных системах (например, двойной старт) —
    # оба должны уйти вместе, а не только один.
    rows = [
        _row(event_date=date(2026, 1, 3), platform_code="five_verst"),
        _row(event_date=date(2026, 1, 10), platform_code="five_verst"),
        _row(event_date=date(2026, 1, 10), platform_code="s95"),
    ]
    before = _rows_before_last_activity(rows)
    assert before is not None
    assert len(before) == 1
    assert before[0].event_date == date(2026, 1, 3)


def test_challenge_recent_delta_via_manual_diff() -> None:
    """Механика recent_delta в compute_challenges — сравнение current с
    результатом того же челленджа без последнего дня активности."""
    rows = [
        _row(finish_time_sec=25 * 60 + 13, event_date=date(2026, 1, 3)),
        _row(finish_time_sec=26 * 60 + 14, event_date=date(2026, 1, 10)),
    ]
    full = _seconds_challenge(rows)
    before = _seconds_challenge(_rows_before_last_activity(rows) or [])
    assert full["current"] - before["current"] == 1


def test_goal_progress_runs_year_recent_delta() -> None:
    goal = UserGoal(year=2026, goal_type="runs_year", target_value=50)
    rows_before = [_row(event_date=date(2026, 1, 3))]
    rows = rows_before + [_row(event_date=date(2026, 1, 10))]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 1, 10), rows_before=rows_before)
    assert result["recent_delta"] == 1


def test_goal_progress_no_rows_before_gives_zero_delta() -> None:
    goal = UserGoal(year=2026, goal_type="runs_year", target_value=50)
    rows = [_row(event_date=date(2026, 1, 3))]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 1, 10))
    assert result["recent_delta"] == 0


def test_goal_progress_finish_under_recent_delta_on_improvement() -> None:
    goal = UserGoal(year=2026, goal_type="finish_under", target_value=1500)
    rows_before = [_row(event_date=date(2026, 1, 3), finish_time_sec=1600)]
    rows = rows_before + [_row(event_date=date(2026, 1, 10), finish_time_sec=1550)]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 1, 10), rows_before=rows_before)
    assert result["recent_delta"] == 50


def test_goal_progress_finish_under_recent_delta_zero_if_slower() -> None:
    goal = UserGoal(year=2026, goal_type="finish_under", target_value=1500)
    rows_before = [_row(event_date=date(2026, 1, 3), finish_time_sec=1550)]
    rows = rows_before + [_row(event_date=date(2026, 1, 10), finish_time_sec=1600)]
    result = _goal_progress(goal, rows=rows, vol_rows={}, today=date(2026, 1, 10), rows_before=rows_before)
    assert result["recent_delta"] == 0
