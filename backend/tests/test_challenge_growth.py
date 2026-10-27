"""Лестницы роста челленджей — общая основа карточки и рейтинга."""

import random
from datetime import date, timedelta

from app.challenge_growth import (
    best_year_growth,
    calendar_day_key,
    current_streak_growth,
    first_dates,
    hirsch_growth,
    position_key,
    seconds_key,
    start_number_key,
    streak_growth,
    wilson_floating_growth,
    wilson_growth,
)
from app.saturday_week import max_saturday_streak
from app.services.achievements_service import _wilson_chains


def _h_index(counts: list[int]) -> int:
    value = 0
    for index, count in enumerate(sorted(counts, reverse=True), start=1):
        if count >= index:
            value = index
    return value


def test_hirsch_growth_matches_full_recount() -> None:
    """Проход без пересортировки обязан давать то же, что честный пересчёт
    индекса после каждого визита (так считала карточка до 26.10.2026)."""
    rng = random.Random(42)
    for _ in range(200):
        start = date(2023, 1, 7)
        visits = [
            (start + timedelta(days=7 * step), rng.choice("abcdefg"))
            for step in range(rng.randint(0, 60))
        ]
        expected: list[date] = []
        counts: dict[str, int] = {}
        for event_date, location in visits:
            counts[location] = counts.get(location, 0) + 1
            while _h_index(list(counts.values())) > len(expected):
                expected.append(event_date)
        assert hirsch_growth(visits) == expected


def test_hirsch_growth_needs_both_breadth_and_depth() -> None:
    day = date(2025, 1, 4)
    # Двадцать финишей на одной локации — индекс 1, а не 20.
    assert len(hirsch_growth([(day, "home")] * 20)) == 1
    # Две локации по два визита — индекс 2, поднятый вторым визитом второй.
    visits = [(day, "a"), (day + timedelta(7), "b"), (day + timedelta(14), "a"), (day + timedelta(21), "b")]
    assert hirsch_growth(visits) == [day, day + timedelta(21)]


def test_wilson_growth_jumps_when_a_gap_is_filled() -> None:
    d1, d2, d3, d4 = date(2025, 1, 4), date(2025, 1, 11), date(2025, 1, 18), date(2025, 1, 25)
    growth = wilson_growth([(d1, 1), (d2, 2), (d3, 4), (d4, 3)])
    # №3 закрыл дыру — цепочка выросла с 2 сразу до 4, дата повторена.
    assert growth == [d1, d2, d4, d4]
    # Номер без №1 цепочку не начинает, пустые номера пропускаются.
    assert wilson_growth([(d1, 2), (d2, None), (d3, 0)]) == []


def test_wilson_floating_matches_card_rule() -> None:
    """Плавающая цепочка рейтинга — та же длина, что у карточки
    (_wilson_chains), а дата шага — когда эта длина впервые набрана."""
    rng = random.Random(3)
    base = date(2024, 1, 6)
    for _ in range(300):
        numbers = [(base + timedelta(7 * step), rng.randint(1, 40)) for step in range(rng.randint(0, 50))]
        growth, start = wilson_floating_growth(numbers)
        _classic, floating, _floating_start = _wilson_chains({n for _d, n in numbers})
        assert len(growth) == floating
        if floating:
            chain = set(range(start, start + floating))
            assert chain <= {n for _d, n in numbers}
            # Цепочка целиком собрана к дате последнего шага, но не раньше.
            assert chain <= {n for d, n in numbers if d <= growth[-1]}


def test_wilson_floating_merges_segments() -> None:
    d1, d2, d3 = date(2025, 1, 4), date(2025, 1, 11), date(2025, 1, 18)
    growth, start = wilson_floating_growth([(d1, 10), (d1, 11), (d2, 13), (d2, 14), (d3, 12)])
    # №12 склеил 10–11 и 13–14 в одну цепочку из пяти.
    assert growth == [d1, d1, d3, d3, d3]
    assert start == 10
    # Повтор номера и пустые номера ничего не меняют.
    assert wilson_floating_growth([(d1, 5), (d2, 5), (d3, None)]) == ([d1], 5)


def test_streak_growth_counts_weeks_and_dates_the_first_start_of_the_week() -> None:
    saturday = date(2025, 10, 4)
    dates = [saturday, saturday + timedelta(7), saturday + timedelta(14)]
    assert streak_growth(dates) == dates
    # Пропуск недели рвёт серию: лучшая остаётся 3, вторая серия её не растит.
    after_gap = [saturday + timedelta(28), saturday + timedelta(35)]
    assert streak_growth(dates + after_gap) == dates
    # Будний старт (1 января в среду) — неделя засчитана и датирована днём
    # старта, а не субботой той же недели, которая ещё впереди.
    wednesday = date(2025, 1, 1)
    assert streak_growth([wednesday]) == [wednesday]
    # Два старта одной недели серию не удлиняют.
    assert len(streak_growth([saturday, saturday + timedelta(1)])) == 1


def test_streak_growth_length_matches_calendar_rule() -> None:
    rng = random.Random(7)
    base = date(2024, 1, 6)
    for _ in range(100):
        dates = {base + timedelta(days=rng.randint(0, 400)) for _ in range(rng.randint(0, 40))}
        assert len(streak_growth(dates)) == max_saturday_streak(dates)


def test_current_streak_lives_through_the_week_before() -> None:
    """Текущая серия: до недели последнего старта базы или предыдущей —
    иначе она «сгорала» бы каждый понедельник до ближайшей субботы."""
    saturday = date(2026, 10, 10)
    dates = [saturday - timedelta(7 * step) for step in range(4)]
    assert len(current_streak_growth(dates, saturday)) == 4
    assert len(current_streak_growth(dates, saturday + timedelta(7))) == 4
    assert current_streak_growth(dates, saturday + timedelta(14)) == []
    # Разрыв: считается только последний отрезок.
    assert len(current_streak_growth([*dates, saturday - timedelta(42)], saturday)) == 4


def test_best_year_growth_is_the_running_record() -> None:
    runs = [date(2023, 3, 4), date(2023, 3, 11), date(2024, 1, 6), date(2024, 1, 13), date(2024, 1, 20)]
    # 2023 дал 2, 2024 сравнялся на второй пробежке и обогнал на третьей.
    assert best_year_growth(runs) == [date(2023, 3, 4), date(2023, 3, 11), date(2024, 1, 20)]


def test_first_dates_skips_rows_without_a_cell() -> None:
    d1, d2 = date(2025, 1, 4), date(2025, 1, 11)
    assert first_dates([(None, d1), ("x", d1), ("x", d2), ("y", d2)]) == [d1, d2]


def test_cell_keys() -> None:
    assert seconds_key(25 * 60 + 7) == 7
    # Финиш дольше часа и пустое время — не клетка «60 секунд».
    assert seconds_key(3600) is None
    assert seconds_key(None) is None
    assert position_key(118) == 18
    assert position_key(0) is None
    assert calendar_day_key(date(2024, 2, 29)) == (2, 29)
    assert start_number_key(200, 1, 200) == 200
    assert start_number_key(201, 1, 200) is None
