"""Юнит-тесты чистой логики achievements_service (без БД)."""

from __future__ import annotations

from datetime import date, timedelta

from app.models import UserGoal
from app.saturday_week import max_saturday_streak
from app.services.achievements_service import (
    CHALLENGE_TIERS,
    PLAN_SPECS,
    REVIEW_MIN_COMMENT_LEN,
    RatingRow,
    RunRow,
    VolunteerRoleRow,
    _alphabet_challenge,
    _alphabet_tiers,
    _best_year_challenge,
    _best_year_level_dates,
    _build_challenge_list,
    _calendar_days_challenge,
    _club_entry,
    _compute_clubs,
    _countries_challenge,
    _deja_vu_challenge,
    _fibonacci_challenge,
    _first_letter,
    _goal_progress,
    _inspector_challenge,
    _is_prime,
    _level_dates,
    _minute_range_challenge,
    _nelson_challenge,
    _number_match_challenge,
    _p_index_challenge,
    _p_index_level_dates,
    _palindrome_challenge,
    _photo_reporter_challenge,
    _platform_slam_challenge,
    _positions_challenge,
    _primes_challenge,
    _resolve_level,
    _reviewer_challenge,
    _role_master_challenge,
    _rows_before_last_activity,
    _runs_needed_for_p,
    _saturdays_left,
    _saturdays_of_year,
    _scope_by_platform,
    _seconds_challenge,
    _start_numbers_range_challenge,
    _streak_challenge,
    _streak_level_dates,
    _threshold_dates,
    _time_display,
    _upcoming_hint,
    _v_index,
    _v_index_challenge,
    _weekdays_challenge,
    _wilson_chains,
    _wilson_challenge,
    _wilson_level_dates,
    _year_fraction_elapsed,
)
from app.volunteer_role_taxonomy import CANONICAL_ROLE_LABELS


def _row(
    event_date: date = date(2026, 1, 3),
    finish_time_sec: int | None = None,
    position: int | None = None,
    event_number: int | None = None,
    location_name: str = "Кузьминки",
    location_key: str = "kuzminki",
    region: str | None = "Москва",
    country: str | None = "Россия",
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
        country=country,
        platform_code=platform_code,
        is_pr=is_pr,
    )


def _rating_row(
    rated_on: date = date(2026, 1, 4),
    platform_code: str = "five_verst",
    is_review: bool = False,
    has_photo: bool = False,
) -> RatingRow:
    return RatingRow(
        rated_on=rated_on,
        platform_code=platform_code,
        is_review=is_review,
        has_photo=has_photo,
    )


def _role_row(
    event_date: date | None = date(2026, 1, 3),
    role_key: str = "marshal",
    platform_code: str = "five_verst",
    location_name: str = "Кузьминки",
    location_key: str | None = None,
    occasions: int = 1,
) -> VolunteerRoleRow:
    return VolunteerRoleRow(
        event_date=event_date,
        platform_code=platform_code,
        role_key=role_key,
        role_label=CANONICAL_ROLE_LABELS.get(role_key, role_key),
        location_name=location_name,
        location_key=location_key or location_name,
        occasions=occasions,
    )


def _parkrun_row(role_key: str, occasions: int) -> VolunteerRoleRow:
    """Строка из сводки parkrun: волонтёрства есть, дат у них нет."""
    return _role_row(
        event_date=None,
        role_key=role_key,
        platform_code="parkrun",
        location_name="Измайловский",
        occasions=occasions,
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

    role_rows = [
        _role_row(platform_code="five_verst", event_date=date(2026, 1, 3)),
        _role_row(platform_code="s95", event_date=date(2026, 1, 10)),
    ]

    (
        scoped_rows,
        scoped_vol_rows,
        scoped_upcoming,
        scoped_rating_rows,
        scoped_role_rows,
    ) = _scope_by_platform(rows, vol_rows, upcoming, rating_rows, role_rows, "s95")
    assert [row.platform_code for row in scoped_rows] == ["s95"]
    assert set(scoped_vol_rows) == {"s95"}
    assert set(scoped_upcoming) == {("s95", 42)}
    assert [row.platform_code for row in scoped_rating_rows] == ["s95"]
    assert [row.platform_code for row in scoped_role_rows] == ["s95"]

    assert _scope_by_platform(rows, vol_rows, upcoming, rating_rows, role_rows, None) == (
        rows,
        vol_rows,
        upcoming,
        rating_rows,
        role_rows,
    )


def _tier(challenge: dict, tier_key: str) -> dict:
    return next(t for t in challenge["tiers"] if t["tier"] == tier_key)


def test_inspector_counts_every_rating() -> None:
    rows = [_rating_row(rated_on=date(2026, 1, 4) + timedelta(days=i)) for i in range(25)]
    challenge = _inspector_challenge(rows)
    assert challenge["code"] == "inspector"
    assert challenge["current"] == 25
    # easy (1/5/10) закрыт целиком, medium (20/40/70) — только бронза (20-я
    # оценка) — "лучшее" достижение вычисляется по самому сложному тиру,
    # где взят хоть один уровень.
    assert _tier(challenge, "easy")["level"] == "gold"
    assert challenge["best_tier"] == "medium"
    assert challenge["best_level"] == "bronze"
    assert _tier(challenge, "medium")["level_dates"]["bronze"] == (
        date(2026, 1, 4) + timedelta(days=19)
    ).isoformat()


def test_inspector_empty_has_no_level() -> None:
    challenge = _inspector_challenge([])
    assert challenge["current"] == 0
    assert challenge["best_level"] is None
    assert _tier(challenge, "easy")["level_dates"] == {"bronze": None, "silver": None, "gold": None}


def test_reviewer_counts_only_reviews() -> None:
    rows = [
        *[_rating_row(rated_on=date(2026, 1, 4), is_review=False) for _ in range(12)],
        *[_rating_row(rated_on=date(2026, 1, 5), is_review=True) for _ in range(10)],
    ]
    challenge = _reviewer_challenge(rows)
    assert challenge["code"] == "reviewer"
    # Голые звёзды в рецензии не идут — только 10 развёрнутых, что закрывает
    # easy-тир (1/3/7) целиком до золота.
    assert challenge["current"] == 10
    assert challenge["best_tier"] == "easy"
    assert challenge["best_level"] == "gold"


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
        date(2026, 7, 8),  # среда — считается за свою неделю (суббота 11.07)
    }
    assert max_saturday_streak(dates) == 3


def test_streak_challenge_counts_sunday_transfer_as_its_saturday() -> None:
    # 01.11.2025 была рабочей субботой, старты перенесли на воскресенье 02.11.
    # До 16.09.2026 челлендж считал только буквальные субботы и рвал здесь
    # серию, хотя календарь суббот её продолжал.
    dates = [date(2025, 10, 18), date(2025, 10, 25), date(2025, 11, 2), date(2025, 11, 8)]
    challenge = _streak_challenge([_row(event_date=value) for value in dates], {})
    assert challenge["current"] == 4


def test_first_letter_rules() -> None:
    assert _first_letter("Кузьминки") == "К"
    assert _first_letter("Ёлочки") == "Е"
    assert _first_letter(" зелёный парк") == "З"
    assert _first_letter("5-й километр") is None


def _alphabet_names(*pairs: tuple[str, str]) -> dict[str, set[str]]:
    """Каталог «буква -> названия локаций» в том виде, в каком его отдаёт БД."""
    names: dict[str, set[str]] = {}
    for letter, name in pairs:
        names.setdefault(letter, set()).add(name)
    return names


def test_alphabet_letters_limited_to_scope_catalog() -> None:
    """Буквы приходят из каталога локаций: нет локации на «Ц» — нет и клетки."""
    names = _alphabet_names(("К", "Кузьминки"), ("Б", "Битца"))
    challenge = _alphabet_challenge(
        [_row(location_name="Кузьминки", location_key="kuzminki")],
        names,
        platform_code="five_verst",
    )
    letters = challenge["detail"]["letters"]  # type: ignore[index]
    assert [item["letter"] for item in letters] == ["Б", "К"]
    assert [item["done"] for item in letters] == [False, True]
    assert challenge["current"] == 1
    assert challenge["detail"]["available"] == 2  # type: ignore[index]


def test_alphabet_gold_equals_available_letters() -> None:
    """Золото сложного тира — весь доступный алфавит: 28 сквозь все системы,
    26 у 5 вёрст, 17 у S95. Иначе под фильтром обещали бы недостижимое."""
    assert _alphabet_tiers(28) == CHALLENGE_TIERS["alphabet"]
    assert _alphabet_tiers(26)["hard"][2] == 26
    assert _alphabet_tiers(17)["hard"][2] == 17


def test_alphabet_tiers_stay_strictly_increasing() -> None:
    """На строгом росте порогов сквозь тиры держится выбор «лучшего» тира."""
    for available in range(9, 29):
        flat = [value for thresholds in _alphabet_tiers(available).values() for value in thresholds]
        assert flat == sorted(set(flat)), f"пороги не растут строго при {available} буквах"
        assert flat[0] >= 1
        assert flat[-1] == available


def test_alphabet_small_catalog_collapses_to_single_tier() -> None:
    """Букв меньше, чем порогов: три тира не укладываются — остаётся один
    (как у «Семи дней», фронт тогда не рисует вкладки сложности)."""
    assert _alphabet_tiers(6) == {"solo": (2, 4, 6)}
    # Пустой каталог: золото не должно доставаться само собой при нуле букв.
    assert _alphabet_tiers(0)["solo"][2] >= 1


def test_alphabet_challenge_uses_scoped_tiers() -> None:
    names = _alphabet_names(*((letter, f"Локация {letter}") for letter in "АБВГДЕЖЗИКЛМНОПРСТУФХЧШЭЮЯ"))
    challenge = _alphabet_challenge([], names, platform_code="five_verst")
    hard = next(tier for tier in challenge["tiers"] if tier["tier"] == "hard")  # type: ignore[union-attr]
    assert challenge["detail"]["available"] == 26  # type: ignore[index]
    assert hard["target"] == 26
    assert "золото даётся за все 26" in str(challenge["description"])


def test_alphabet_description_names_the_scoped_platform() -> None:
    names = _alphabet_names(("К", "Кузьминки"), ("Б", "Битца"))
    scoped = _alphabet_challenge([], names, platform_code="five_verst")
    assert "5 вёрст" in str(scoped["description"])
    assert "2 буквы" in str(scoped["description"])
    overall = _alphabet_challenge([], names, platform_code=None)
    assert "parkrun" in str(overall["description"])


def test_alphabet_skips_parkrun_only_in_cross_platform_scope() -> None:
    """Сквозной вид parkrun не считает, а в скоупе самого parkrun — считает:
    иначе буквы его русскоязычных локаций были бы недостижимы."""
    names = _alphabet_names(("Т", "Тропарёво"))
    rows = [_row(location_name="Тропарёво", location_key="troparevo", platform_code="parkrun")]

    overall = _alphabet_challenge(rows, names, platform_code=None)
    assert overall["current"] == 0

    scoped = _alphabet_challenge(rows, names, platform_code="parkrun")
    assert scoped["current"] == 1
    assert scoped["detail"]["letters"][0]["done"] is True  # type: ignore[index]


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
    result = _start_numbers_range_challenge(
        rows, {}, code="start_numbers", title="Нумератор", description="", low=1, high=200
    )
    assert result["current"] == 4
    assert result["detail"]["cells"][49]["done"] is True  # type: ignore[index]
    assert result["detail"]["cells"][49]["platform_code"] == "five_verst"  # type: ignore[index]
    assert result["detail"]["cells"][59]["done"] is True  # type: ignore[index]
    assert result["detail"]["cells"][59]["platform_code"] == "s95"  # type: ignore[index]


def test_start_numbers_pro_range() -> None:
    rows = [_row(event_number=250, platform_code="s95"), _row(event_number=399, platform_code="s95")]
    pro = _start_numbers_range_challenge(
        rows, {}, code="start_numbers_pro", title="Нумератор ПРО", description="", low=201, high=400
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


def test_challenge_detail_cell_carries_last_activity_date() -> None:
    """«Детали» подсвечивают свежую клетку, сверяя её date с recent_date —
    датой последнего дня активности. Payload обязан давать ровно такое
    совпадение, иначе «↑ +1» на карточке снова останется числом без адреса."""
    rows = [
        _row(finish_time_sec=25 * 60 + 13, event_date=date(2026, 1, 3)),
        _row(finish_time_sec=26 * 60 + 14, event_date=date(2026, 1, 10)),
    ]
    challenge = _seconds_challenge(rows)
    recent_date = rows[-1].event_date.isoformat()
    cells = challenge["detail"]["cells"]  # type: ignore[index]
    fresh = [cell for cell in cells if cell["date"] == recent_date]
    assert [cell["label"] for cell in fresh] == [":14"]
    # Саму дату проставляет compute_challenges — у отдельного челленджа её нет.
    assert challenge["recent_date"] is None


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


def test_photo_reporter_counts_only_ratings_with_photo() -> None:
    rows = [
        _rating_row(rated_on=date(2026, 1, 4), has_photo=True),
        _rating_row(rated_on=date(2026, 1, 11)),
        _rating_row(rated_on=date(2026, 1, 18), has_photo=True),
        _rating_row(rated_on=date(2026, 1, 25), is_review=True),
    ]
    challenge = _photo_reporter_challenge(rows)
    assert challenge["code"] == "photo_reporter"
    assert challenge["current"] == 2
    easy = _tier(challenge, "easy")
    # Пороги лёгкого тира 1/3/5: две оценки с фото — бронза, до серебра одна.
    assert easy["level"] == "bronze"
    assert easy["to_next_level"] == 1
    assert easy["level_dates"]["bronze"] == "2026-01-04"


def test_photo_reporter_empty_has_no_level() -> None:
    challenge = _photo_reporter_challenge([_rating_row(), _rating_row(is_review=True)])
    assert challenge["current"] == 0
    assert challenge["best_level"] is None


def test_v_index_needs_v_locations_visited_v_times() -> None:
    # Три площадки по три волонтёрства — индекс 3; четвёртая одним разом его не двигает.
    counts = {"fili": 3, "kuzminki": 3, "sokolniki": 3, "izmailovo": 1}
    assert _v_index(counts) == 3
    # Двадцать волонтёрств на своей площадке — это всё ещё индекс 1.
    assert _v_index({"kuzminki": 20}) == 1
    assert _v_index({}) == 0


def test_v_index_challenge_dates_levels_by_history() -> None:
    rows = [
        _role_row(location_name="Кузьминки", event_date=date(2026, 1, 3)),
        _role_row(location_name="Фили", event_date=date(2026, 1, 10)),
        # Индекс 2 берётся только когда на обеих площадках отработано дважды.
        _role_row(location_name="Кузьминки", event_date=date(2026, 1, 17)),
        _role_row(location_name="Фили", event_date=date(2026, 1, 24)),
    ]
    challenge = _v_index_challenge(rows)
    assert challenge["current"] == 2
    easy = _tier(challenge, "easy")
    # Пороги лёгкого тира 2/3/4: бронза взята вторым подъёмом индекса.
    assert easy["level_dates"]["bronze"] == "2026-01-24"
    assert easy["level_dates"]["silver"] is None
    # Детали — площадки с числом волонтёрств, самые обжитые сверху.
    assert challenge["detail"]["items"][0]["count"] == 2


def test_v_index_counts_one_shift_per_day_and_location() -> None:
    # Две роли в одну субботу на одной площадке — одно волонтёрство, а не два.
    rows = [
        _role_row(role_key="marshal", location_name="Кузьминки", event_date=date(2026, 1, 3)),
        _role_row(role_key="timekeeper", location_name="Кузьминки", event_date=date(2026, 1, 3)),
    ]
    challenge = _v_index_challenge(rows)
    assert challenge["current"] == 1
    assert challenge["detail"]["items"][0]["count"] == 1


def test_v_index_to_next_label_counts_missing_volunteerings() -> None:
    # Две площадки по два волонтёрства: до индекса 3 нужны третьи на обеих
    # плюс три на третьей — итого 5.
    rows = [
        _role_row(location_name="Кузьминки", event_date=date(2026, 1, 3)),
        _role_row(location_name="Кузьминки", event_date=date(2026, 1, 10)),
        _role_row(location_name="Фили", event_date=date(2026, 1, 17)),
        _role_row(location_name="Фили", event_date=date(2026, 1, 24)),
    ]
    challenge = _v_index_challenge(rows)
    assert challenge["current"] == 2
    assert _tier(challenge, "easy")["to_next_label"] == "ещё 5 волонтёрств"


def test_role_master_counts_any_distinct_roles() -> None:
    rows = [
        _role_row(role_key="marshal", event_date=date(2026, 1, 3)),
        _role_row(role_key="marshal", event_date=date(2026, 1, 10)),
        _role_row(role_key="timekeeper", event_date=date(2026, 1, 17)),
        # Роли фиксированного списка больше нет: фотограф считается наравне.
        _role_row(role_key="photographer", event_date=date(2026, 1, 24)),
    ]
    challenge = _role_master_challenge(rows)
    assert challenge["current"] == 3
    cells = challenge["detail"]["cells"]
    # Клетки — весь справочник ролей: это меню, а не обязательный список.
    assert len(cells) == len(CANONICAL_ROLE_LABELS)
    by_label = {cell["label"]: cell for cell in cells}
    marshal = by_label[CANONICAL_ROLE_LABELS["marshal"]]
    assert marshal["done"] is True
    assert marshal["date"] == "2026-01-03"
    assert marshal["count"] == 2
    assert marshal["location"] == "Кузьминки"
    assert by_label[CANONICAL_ROLE_LABELS["photographer"]]["done"] is True
    assert by_label[CANONICAL_ROLE_LABELS["run_director"]]["done"] is False


def test_role_master_gold_is_twenty_any_roles() -> None:
    keys = list(CANONICAL_ROLE_LABELS)[:20]
    rows = [
        _role_row(role_key=key, event_date=date(2026, 1, 3) + timedelta(days=index))
        for index, key in enumerate(keys)
    ]
    challenge = _role_master_challenge(rows)
    assert challenge["current"] == 20
    # Три уровня сложности, лестница упирается в 20 ролей на золоте сложного.
    assert [tier["tier"] for tier in challenge["tiers"]] == ["easy", "medium", "hard"]
    hard = _tier(challenge, "hard")
    assert hard["levels"] == {"bronze": 15, "silver": 17, "gold": 20}
    assert hard["level"] == "gold"
    assert challenge["best_tier"] == "hard"


def test_role_master_keeps_unknown_role_as_its_own_cell() -> None:
    # Роль, которой ещё нет в справочнике, идёт в зачёт под своим названием —
    # иначе счётчик занижался бы на свежих данных прода.
    unknown = VolunteerRoleRow(
        event_date=date(2026, 1, 3),
        platform_code="five_verst",
        role_key="raw:novaya_rol",
        role_label="Новая роль",
        location_name="Кузьминки",
        location_key="kuzminki",
    )
    challenge = _role_master_challenge([unknown])
    assert challenge["current"] == 1
    cells = challenge["detail"]["cells"]
    assert len(cells) == len(CANONICAL_ROLE_LABELS) + 1
    assert cells[-1]["label"] == "Новая роль"
    assert cells[-1]["done"] is True


def test_v_index_ignores_parkrun_summary() -> None:
    # Сводка parkrun в V-индекс не идёт вовсе: у волонтёрства нет ни локации, ни даты —
    # ровно тех двух полей, на которых индекс и держится. Девять волонтёрств из сводки
    # дали бы индекс 3, но счётчик остаётся нулевым.
    rows = [
        _parkrun_row("marshal", 3),
        _parkrun_row("timekeeper", 3),
        _parkrun_row("barcode_scanning", 3),
    ]
    challenge = _v_index_challenge(rows)
    assert challenge["current"] == 0
    assert challenge["best_level"] is None
    assert challenge["detail"]["items"] == []


def test_v_index_counts_only_dated_shifts_next_to_parkrun() -> None:
    # Десять parkrun-волонтёрств игнорируются целиком: индекс набирается только
    # датированными стартами, и датируется по ним же.
    rows = [
        _parkrun_row("marshal", 5),
        _parkrun_row("timekeeper", 5),
        _role_row(location_name="Кузьминки", event_date=date(2026, 1, 3)),
        _role_row(location_name="Кузьминки", event_date=date(2026, 1, 10)),
        _role_row(location_name="Фили", event_date=date(2026, 1, 17)),
        _role_row(location_name="Фили", event_date=date(2026, 1, 24)),
    ]
    challenge = _v_index_challenge(rows)
    # Со сводкой было бы 5, без неё — две площадки по два волонтёрства.
    assert challenge["current"] == 2
    easy = _tier(challenge, "easy")
    assert easy["level"] == "bronze"
    assert easy["level_dates"]["bronze"] == "2026-01-24"
    assert [item["count"] for item in challenge["detail"]["items"]] == [2, 2]


def test_role_master_counts_parkrun_summary_roles() -> None:
    # Асимметрия намеренная: V-индекс сводку parkrun игнорирует (см.
    # test_v_index_ignores_parkrun_summary), а чек-лист — учитывает: на вопрос
    # «выходил ли на роль вообще» сводка отвечает честно.
    rows = [
        _parkrun_row("marshal", 4),
        _parkrun_row("timekeeper", 1),
        _role_row(role_key="run_director", event_date=date(2026, 1, 3)),
    ]
    challenge = _role_master_challenge(rows)
    assert challenge["current"] == 3
    by_label = {cell["label"]: cell for cell in challenge["detail"]["cells"]}
    assert by_label[CANONICAL_ROLE_LABELS["run_director"]]["done"] is True
    marshal = by_label[CANONICAL_ROLE_LABELS["marshal"]]
    assert marshal["done"] is True
    assert marshal["date"] is None
    assert marshal["count"] == 4
    assert marshal["count_label"] == "4 волонтёрства"
    assert "parkrun" in str(marshal["hint"])


def test_role_master_prefers_dated_row_over_parkrun_summary() -> None:
    # Роль закрыта и сводкой parkrun, и обычным стартом: в клетке показываем
    # датированный — «закрыто 03.01.26 в Кузьминках» полезнее «без даты».
    rows = [
        _parkrun_row("marshal", 4),
        _role_row(role_key="marshal", event_date=date(2026, 1, 3)),
    ]
    challenge = _role_master_challenge(rows)
    by_label = {cell["label"]: cell for cell in challenge["detail"]["cells"]}
    marshal = by_label[CANONICAL_ROLE_LABELS["marshal"]]
    assert marshal["date"] == "2026-01-03"
    assert marshal["location"] == "Кузьминки"
    assert marshal["count"] == 5


# ---------------------------------------------------------------------------
# Ч48/Ч51/Ч23/Ч26/Ч27/Ч28/Ч29 — челленджи из бэклога сообщества


def test_platform_slam_needs_a_finish_not_just_a_start() -> None:
    """Условие Дмитрия (10.09.2026) — именно ФИНИШ в каждой из четырёх систем.
    Строка без времени (снятый протокол, DNF) систему не открывает."""
    rows = [
        _row(platform_code="five_verst", finish_time_sec=1500),
        _row(platform_code="s95", finish_time_sec=1510),
        _row(platform_code="parkrun", finish_time_sec=None),
    ]
    challenge = _platform_slam_challenge(rows)
    assert challenge["current"] == 2
    cells = {cell["label"]: cell for cell in challenge["detail"]["cells"]}  # type: ignore[index,union-attr]
    assert cells["5 вёрст"]["done"] is True
    assert cells["parkrun"]["done"] is False
    assert cells["RunPark"]["done"] is False


def test_platform_slam_counts_all_four_systems() -> None:
    rows = [
        _row(platform_code=code, finish_time_sec=1500)
        for code in ("five_verst", "s95", "parkrun", "runpark")
    ]
    challenge = _platform_slam_challenge(rows)
    assert challenge["current"] == 4
    assert challenge["best_level"] == "gold"


def test_platform_slam_hidden_under_platform_filter() -> None:
    """Под фильтром одной системы «Хет-трик» показывал бы вечную единицу из
    четырёх — в разрезе платформы карточки просто нет."""
    rows = [_row(platform_code="five_verst", finish_time_sec=1500)]
    codes_all = {
        c["code"]
        for c in _build_challenge_list(
            rows, {}, {}, [], [], alphabet_names={}, platform_code=None, home_country="Россия"
        )
    }
    codes_scoped = {
        c["code"]
        for c in _build_challenge_list(
            rows, {}, {}, [], [], alphabet_names={}, platform_code="five_verst", home_country="Россия"
        )
    }
    assert "platform_slam" in codes_all
    assert "platform_slam" not in codes_scoped


def test_countries_challenge_marks_home_country_first() -> None:
    """Дом задаётся домашней локацией, а не «Россией по умолчанию»: у бегуна
    из Белграда международный старт — как раз российский."""
    rows = [
        _row(country="Сербия", finish_time_sec=1500, location_name="Ада Циганлия"),
        _row(country="Сербия", finish_time_sec=1505, location_name="Ада Циганлия"),
        _row(country="Россия", finish_time_sec=1600, location_name="Кузьминки"),
    ]
    challenge = _countries_challenge(rows, "Сербия")
    assert challenge["current"] == 2
    items = challenge["detail"]["items"]  # type: ignore[index]
    assert items[0]["value"].endswith("Сербия") and items[0]["value"].startswith("🏠")
    assert items[1]["value"] == "Россия"
    assert "Дом — Сербия" in str(challenge["detail"]["note"])  # type: ignore[index]


def test_countries_challenge_shows_unknown_country_gap() -> None:
    """Площадки без страны (часть мирового каталога parkrun) в счётчик не идут,
    но и не молчат: иначе дыра в данных читалась бы как занижение бейджа."""
    rows = [
        _row(country="Россия", finish_time_sec=1500),
        _row(country=None, finish_time_sec=1500, location_name="Bushy Park"),
    ]
    challenge = _countries_challenge(rows, "Россия")
    assert challenge["current"] == 1
    assert challenge["detail"]["items"][-1]["count"] == 1  # type: ignore[index]


def test_minute_range_counts_closed_buckets_not_the_span() -> None:
    """Правка Дмитрия 14.09.2026: две пробежки, 24:xx и 38:xx, — это ДВЕ
    корзины, а не пятнадцать. Лента при этом рисуется на весь диапазон, и
    пустые клетки внутри — то, что осталось собрать."""
    rows = [
        _row(finish_time_sec=24 * 60 + 10),
        _row(finish_time_sec=38 * 60 + 5),
    ]
    challenge = _minute_range_challenge(rows)
    assert challenge["current"] == 2
    cells = challenge["detail"]["cells"]  # type: ignore[index]
    assert len(cells) == 15  # 24…38 включительно
    assert cells[0]["done"] is True and cells[-1]["done"] is True
    assert [cell["label"] for cell in cells if not cell["done"]][:2] == ["25", "26"]
    assert "24:xx — 38:xx" in str(challenge["detail"]["note"])  # type: ignore[index]


def test_minute_range_repeat_does_not_add_a_bucket() -> None:
    rows = [_row(finish_time_sec=25 * 60 + 5), _row(finish_time_sec=25 * 60 + 50)]
    assert _minute_range_challenge(rows)["current"] == 1


def test_minute_range_level_dates_follow_new_buckets() -> None:
    """Уровень датируется днём, когда закрыта k-я корзина, — новая минута
    засчитывается хоть быстрее прежних, хоть медленнее."""
    rows = [
        _row(event_date=date(2026, 1, 3), finish_time_sec=25 * 60),
        _row(event_date=date(2026, 1, 10), finish_time_sec=25 * 60 + 40),  # та же корзина
        _row(event_date=date(2026, 1, 17), finish_time_sec=29 * 60),
        _row(event_date=date(2026, 1, 24), finish_time_sec=21 * 60),
    ]
    challenge = _minute_range_challenge(rows)
    assert challenge["current"] == 3
    tier = next(t for t in challenge["tiers"] if t["tier"] == "easy")  # type: ignore[union-attr]
    assert tier["level_dates"]["bronze"] == "2026-01-24"  # третья корзина


def test_minute_range_ignores_impossible_times() -> None:
    rows = [_row(finish_time_sec=59), _row(finish_time_sec=25 * 60), _row(finish_time_sec=None)]
    assert _minute_range_challenge(rows)["current"] == 1


def test_wilson_chains_classic_and_floating() -> None:
    # 1,2,3 — классическая цепочка; 10..14 — самая длинная плавающая
    numbers = {1, 2, 3, 10, 11, 12, 13, 14, 20}
    classic, floating, start = _wilson_chains(numbers)
    assert (classic, floating, start) == (3, 5, 10)


def test_wilson_without_first_number_has_zero_classic() -> None:
    challenge = _wilson_challenge([_row(event_number=n) for n in (7, 8, 9)], {})
    assert challenge["current"] == 0
    note = str(challenge["detail"]["note"])  # type: ignore[index]
    assert "нужен старт №1" in note
    assert "Самая длинная цепочка — 3 (№7–№9)" in note


def test_wilson_marks_both_chains_on_one_strip() -> None:
    """Просьба Дмитрия 11.09.2026: не два блока, а одна лента, где видно, где
    цепочка рвётся. Клетки помечаются группой, а не рисуются дважды."""
    challenge = _wilson_challenge([_row(event_number=n) for n in (1, 2, 20, 21, 22, 23)], {})
    cells = {int(cell["label"]): cell for cell in challenge["detail"]["cells"]}  # type: ignore[index,union-attr]
    assert cells[1]["accent"] == "classic" and cells[2]["accent"] == "classic"
    assert cells[3]["accent"] is None
    assert [n for n in (20, 21, 22, 23) if cells[n]["accent"] == "floating"] == [20, 21, 22, 23]


def test_wilson_cell_hint_says_where_to_take_the_next_link() -> None:
    """Планирование живёт в модалке (правка Дмитрия 14.09.2026), а подсказка у
    клетки отвечает на «где взять именно этот номер»."""
    planned = {("five_verst", 3): [(date(2027, 2, 14), "Битца")]}
    challenge = _wilson_challenge([_row(event_number=n) for n in (1, 2)], planned)
    cells = {cell["label"]: cell for cell in challenge["detail"]["cells"]}  # type: ignore[index,union-attr]
    assert cells["3"]["hint"] == "Где взять: Битца ≈ 14.02.27"
    assert "items" not in challenge["detail"]


def test_wilson_level_dates_follow_the_chain() -> None:
    rows = [
        _row(event_date=date(2026, 1, 3), event_number=2),
        _row(event_date=date(2026, 1, 10), event_number=1),  # цепочка стала 1–2
        _row(event_date=date(2026, 1, 17), event_number=3),
    ]
    dates = _wilson_level_dates(rows, {"bronze": 1, "silver": 2, "gold": 3})
    assert dates["bronze"] == "2026-01-10"
    assert dates["silver"] == "2026-01-10"  # №1 сразу вытянул цепочку до двух
    assert dates["gold"] == "2026-01-17"


def test_nelson_counts_distinct_multiples_of_111() -> None:
    """С 11.09.2026 это коллекция, а не счётчик финишей: два старта №111 на
    разных площадках закрывают одну клетку, а всего финишей видно в подписи."""
    rows = [_row(event_number=111), _row(event_number=111), _row(event_number=222), _row(event_number=150)]
    challenge = _nelson_challenge(rows, {})
    assert challenge["current"] == 2
    assert len(challenge["detail"]["cells"]) == 9  # №111…№999  # type: ignore[arg-type]
    # Один и тот же номер, взятый дважды, клетку не удваивает — но счётчик
    # финишей остаётся в подсказке клетки.
    cells = {cell["label"]: cell for cell in challenge["detail"]["cells"]}  # type: ignore[index,union-attr]
    assert cells["111"]["count"] == 2


def test_nelson_plans_where_to_get_the_missing_ones() -> None:
    """Оговорка «без анонсов будущих дат» снята (решение Дмитрия 11.09.2026):
    карточка обязана показывать, где наступит следующий нельсон."""
    planned = {
        ("five_verst", 222): [(date(2027, 2, 14), "Битца"), (date(2027, 2, 21), "Сормовский")],
        ("s95", 222): [(date(2027, 3, 6), "Ярославль")],
    }
    challenge = _nelson_challenge([_row(event_number=111)], planned)
    assert "без анонсов" not in str(challenge["description"])
    cells = {cell["label"]: cell for cell in challenge["detail"]["cells"]}  # type: ignore[index,union-attr]
    # Номер закрывается стартом в ЛЮБОЙ системе — прогнозы систем в одной подсказке.
    assert cells["222"]["hint"] == "Где взять: Битца ≈ 14.02.27, Сормовский ≈ 21.02.27, Ярославль ≈ 06.03.27"
    assert cells["111"]["hint"] is None  # закрытому подсказка не нужна


def test_plan_specs_cover_every_challenge_with_a_plan_button() -> None:
    """Сторож: кнопка «Планирование →» на фронте зажигается по списку кодов, и
    у каждого из них обязан быть PLAN_SPEC — иначе модалка ответит 404."""
    assert set(PLAN_SPECS) == {
        "start_numbers",
        "start_numbers_pro",
        "fibonacci",
        "nelson",
        "primes",
        "wilson",
        "jubilee",
        "number_match",
    }
    for code, spec in PLAN_SPECS.items():
        numbers = spec.numbers(200)
        assert numbers, code
        assert len(spec.column_titles) == spec.columns, code
        assert spec.horizon_label, code


def test_number_match_plan_follows_your_run_count() -> None:
    """Строки «Совпадения номеров» — номера БУДУЩИХ пробежек: у кого 202
    позади, следующая попытка это старт №203."""
    numbers = PLAN_SPECS["number_match"].numbers(202)
    assert numbers[0] == 203
    assert numbers[-1] == 212
    # Счётчик, а не коллекция: отметки «закрыто» в таблице быть не должно.
    assert PLAN_SPECS["number_match"].tracks_done is False


def test_jubilee_plan_lists_round_numbers() -> None:
    numbers = PLAN_SPECS["jubilee"].numbers(0)
    assert numbers[0] == 50 and numbers[-1] == 400
    assert all(number % 50 == 0 for number in numbers)
    assert PLAN_SPECS["jubilee"].tracks_done is False


def test_fibonacci_is_a_fifteen_cell_collection() -> None:
    rows = [_row(event_number=n) for n in (1, 2, 3, 4, 8, 8)]
    challenge = _fibonacci_challenge(rows, {})
    assert challenge["current"] == 4  # 1, 2, 3, 8 — №4 не из ряда, дубль не считается
    assert len(challenge["detail"]["cells"]) == 15  # type: ignore[arg-type]


def test_fibonacci_cell_hint_says_where_and_when() -> None:
    """«Для каждого числа прописать, кто и когда будет бегать нужный старт»
    (Дмитрий 11.09.2026) — подсказка незакрытой клетки."""
    planned = {("five_verst", 144): [(date(2027, 2, 14), "Раменское")]}
    challenge = _fibonacci_challenge([_row(event_number=1)], planned)
    cells = {cell["label"]: cell for cell in challenge["detail"]["cells"]}  # type: ignore[index,union-attr]
    assert cells["144"]["hint"] == "Где взять: Раменское ≈ 14.02.27"
    assert cells["1"]["hint"] is None  # закрытым подсказка не нужна


def test_primes_is_a_collection_of_distinct_numbers() -> None:
    """С 11.09.2026 счётчик — РАЗНЫЕ простые номера (плитками, как Фибоначчи),
    а не финиши на них."""
    rows = [_row(event_number=n) for n in (2, 3, 4, 5, 9, 11, 11)]
    challenge = _primes_challenge(rows, {})
    assert challenge["current"] == 4  # 2, 3, 5, 11 — дубль №11 клетку не удваивает
    cells = challenge["detail"]["cells"]  # type: ignore[index]
    assert len(cells) == 78  # все простые до №400
    assert cells[0]["label"] == "2" and cells[-1]["label"] == "397"


def test_primes_ignores_numbers_above_the_strip() -> None:
    """№401+ бывают только у зарубежного parkrun, куда прогноз всё равно не
    ходит: в коллекцию они не идут."""
    challenge = _primes_challenge([_row(event_number=401), _row(event_number=3)], {})
    assert challenge["current"] == 1


def test_is_prime_edge_cases() -> None:
    assert [n for n in range(1, 20) if _is_prime(n)] == [2, 3, 5, 7, 11, 13, 17, 19]
