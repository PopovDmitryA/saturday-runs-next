from datetime import date

from app.services.leaderboard_service import (
    LEADERBOARD_METRICS,
    METRIC_META,
    METRIC_THRESHOLD_PERCENTILE,
    PLATFORM_COLUMNS,
    _percentile,
    _ranked,
    _week_start,
)


def test_ranked_basic_order() -> None:
    values = [329, 260, 251, 249, 243]
    assert _ranked(values, 329) == 1
    assert _ranked(values, 243) == 5
    assert _ranked(values, 1000) == 1
    assert _ranked(values, 0) == 6


def test_ranked_ties_share_rank() -> None:
    # RANK-семантика: одинаковые значения делят место, следующее — со скачком.
    values = [165, 165, 164, 100]
    assert _ranked(values, 165) == 1
    assert _ranked(values, 164) == 3
    assert _ranked(values, 100) == 4
    assert _ranked(values, 150) == 4


def test_ranked_empty() -> None:
    assert _ranked([], 5) == 1


def test_week_start_is_seven_day_window() -> None:
    # Окно недели включает саму последнюю дату: [latest-6, latest].
    assert _week_start(date(2026, 7, 11)) == date(2026, 7, 5)


def test_metric_registry_consistent() -> None:
    assert set(METRIC_META) == set(LEADERBOARD_METRICS)
    assert set(METRIC_THRESHOLD_PERCENTILE) == set(LEADERBOARD_METRICS)
    assert len(PLATFORM_COLUMNS) == 4


def test_percentile_basic() -> None:
    # По убыванию: 1..10 (100 в начале — самый большой).
    values_desc = list(range(10, 0, -1))
    assert _percentile(values_desc, 100) == 10
    assert _percentile(values_desc, 0) == 1
    assert _percentile(values_desc, 50) in (5, 6)


def test_percentile_matches_prod_investigation_shape() -> None:
    # Форма распределения как у «Локаций» на проде: подавляющее большинство — 1.
    values_desc = sorted([1] * 82 + [2] * 10 + [3] * 5 + [10] * 3, reverse=True)
    assert _percentile(values_desc, 95) >= 2


def test_percentile_empty() -> None:
    assert _percentile([], 75) == 0
