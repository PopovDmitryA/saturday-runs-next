from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Location
from app.services.dashboard_service import (
    _avg_vs_field_pct,
    _collect_field_comparison_pairs,
    _current_saturday_streak,
    _earned_run_clubs,
    _finish_stability,
    _max_saturday_streak,
    _next_run_club,
    _next_run_milestone,
    _resolve_field_avg_sec,
    _saturday_consistency,
    _saturday_streak,
    _week_saturday,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.user_location_stats import count_unique_geo_from_rows


def test_next_run_milestone() -> None:
    assert _next_run_milestone(0) == (10, 10)
    assert _next_run_milestone(9) == (10, 1)
    assert _next_run_milestone(50) == (100, 50)
    assert _next_run_milestone(1000) == (None, None)


def test_run_clubs() -> None:
    assert _earned_run_clubs(49) == []
    assert _earned_run_clubs(50) == [50]
    assert _earned_run_clubs(150) == [50, 100]
    assert _next_run_club(150) == 250


def test_avg_vs_field_pct() -> None:
    assert _avg_vs_field_pct([(1139, 1200)]) == 5.1
    assert _avg_vs_field_pct([]) is None


def test_resolve_field_avg_sec_prefers_summary() -> None:
    assert _resolve_field_avg_sec(1200, 1100, 50) == 1200
    assert _resolve_field_avg_sec(None, 1100, 3) == 1100
    assert _resolve_field_avg_sec(None, 1100, 1) is None


def test_collect_field_comparison_pairs() -> None:
    pairs = _collect_field_comparison_pairs([(1000, None, 1100, 3), (1000, None, 1000, 1)])
    assert pairs == [(1000, 1100)]
    assert _avg_vs_field_pct(pairs) == 9.1


def test_saturday_consistency() -> None:
    today = date(2026, 5, 27)  # Wednesday
    activity_dates = {date(2026, 5, 23), date(2026, 5, 16)}
    pct, active, total = _saturday_consistency(activity_dates, today)
    assert active == 2
    assert total >= 50
    assert pct == round(active / total * 100, 1)


def test_week_saturday_maps_any_weekday_to_its_week_saturday() -> None:
    saturday = date(2026, 5, 23)
    assert _week_saturday(saturday) == saturday
    assert _week_saturday(date(2026, 5, 20)) == saturday  # среда той же недели
    assert _week_saturday(date(2026, 5, 18)) == saturday  # понедельник той же недели
    assert _week_saturday(date(2026, 5, 24)) == saturday  # воскресенье, закрывает ту же неделю
    assert _week_saturday(date(2026, 5, 17)) == date(2026, 5, 16)  # воскресенье прошлой недели


def test_week_saturday_keeps_sunday_transfer_in_its_own_saturday() -> None:
    # 01.11.2025 была рабочей субботой, и старты перенесли на воскресенье
    # 02.11 — это та же неделя, а не следующая (репорт Дмитрия Евлаша).
    assert _week_saturday(date(2025, 11, 2)) == date(2025, 11, 1)
    assert _max_saturday_streak(
        {date(2025, 10, 25), date(2025, 11, 2), date(2025, 11, 8)}
    ) == 3


def test_saturday_streak_counts_by_week_not_extra_starts_same_week() -> None:
    # Суббота 23.05 + внеплановый старт в среду той же недели (20.05) — одна
    # неделя, не должно давать +1 к серии сверх обычного.
    activity_dates = {date(2026, 5, 9), date(2026, 5, 16), date(2026, 5, 20), date(2026, 5, 23)}
    assert _saturday_streak(activity_dates) == 3


def test_max_saturday_streak_merges_same_week_dates() -> None:
    # 02.05 (суббота) и 06.05 (среда той же недели, закрывается субботой 09.05)
    # вместе с 09.05 (суббота) должны дать серию 2, а не 3 — 06.05 и 09.05
    # это один и тот же старт-неделя. 30.05 — отдельная несмежная неделя.
    activity_dates = {date(2026, 5, 2), date(2026, 5, 6), date(2026, 5, 9), date(2026, 5, 30)}
    assert _max_saturday_streak(activity_dates) == 2


def test_current_saturday_streak_counts_midweek_start_toward_its_week() -> None:
    today = date(2026, 5, 16)  # Saturday
    # Старт в среду 13.05 закрывается субботой 16.05 (той же неделей, что и
    # today) — этого достаточно, отдельного старта в саму субботу не нужно.
    activity_dates = {date(2026, 5, 9), date(2026, 5, 13)}
    assert _current_saturday_streak(activity_dates, today) == 2


def test_count_unique_geo_merges_moscow_region_variants(db_session: Session) -> None:
    # Явный id — иначе оба неперсистентных Location получат одинаковый
    # fallback-ключ кластеризации ("location:None") и ложно схлопнутся в один.
    loc_mo = Location(
        id=uuid4(),
        platform_id=uuid4(),
        external_key="a",
        name="A",
        region="Московская область",
        city="Мытищи",
    )
    loc_m = Location(
        id=uuid4(),
        platform_id=uuid4(),
        external_key="b",
        name="B",
        region="Московская",
        city="Королёв",
    )
    catalog_index = LocationCatalogIndex(db_session)
    regions, cities = count_unique_geo_from_rows(
        catalog_index, [(loc_mo, "five_verst"), (loc_m, "five_verst")]
    )
    assert regions == 1
    assert cities == 2


def test_finish_stability_needs_enough_runs() -> None:
    """Три финиша — ещё не «ровность»: разброс по такой выборке скачет от
    одного случайного дня, плитку показывать не на чем."""
    assert _finish_stability([1500, 1510, 1520]) == (None, 0, 0)


def test_finish_stability_uses_last_window_only() -> None:
    """Форма прошлых лет к сегодняшней ровности отношения не имеет: в окно
    попадают только последние десять финишей."""
    old_chaos = [600, 3000, 700, 2900]
    recent = [1500, 1502, 1498, 1500, 1501, 1499, 1500, 1500, 1502, 1498]
    spread, window, _metronome = _finish_stability(old_chaos + recent)
    assert window == 10
    assert spread <= 2


def test_finish_stability_metronome_streak_spans_whole_history() -> None:
    """Серию метронома, наоборот, ищем по всей истории — это редкое
    достижение, и обрезать его окном значило бы прятать самое интересное."""
    # Пять финишей подряд в коридоре ±30 с, затем выход из него.
    finishes = [1500, 1510, 1520, 1530, 1540, 1800, 1801]
    _spread, _window, metronome = _finish_stability(finishes)
    assert metronome == 5


def test_finish_stability_corridor_is_plus_minus_thirty() -> None:
    # 1500 и 1561 — 61 секунда, в коридор ±30 (ширина 60) уже не влезают,
    # поэтому серия рвётся на паре, а не тянется через всю тройку.
    _spread, _window, metronome = _finish_stability([1500, 1530, 1561, 1600])
    assert metronome == 2
