from datetime import date

from app.services.leaderboard_service import (
    GENDERED_METRICS,
    GENDERED_PLATFORM_COLUMNS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    METRIC_META,
    METRIC_THRESHOLD_PERCENTILE,
    MIN_VISITS_METRICS,
    PLATFORM_COLUMNS,
    PLATFORM_FILTER_METRICS,
    PLATFORM_FILTER_VALUES,
    VOLUNTEER_LOCATION_PLATFORM_COLUMNS,
    WIN_EXTRAS_METRICS,
    _apply_last_win,
    _cache_key,
    _dominant_gender,
    _Entity,
    _LocationVisits,
    _merge_visit_row,
    _normalize_gender,
    _normalize_min_visits,
    _normalize_platform_filter,
    _percentile,
    _pick_home,
    _pick_last,
    _ranked,
    _week_start,
    metric_description,
    platform_columns_for,
    platform_filter_values,
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


def test_win_metrics_threshold_is_minimum() -> None:
    # У победных метрик перцентиль 0 → порог = минимальное значение (1 победа):
    # сама победа уже редкое событие, дополнительного порога входа нет.
    values_desc = sorted([1] * 90 + [2] * 7 + [15] * 3, reverse=True)
    assert _percentile(values_desc, METRIC_THRESHOLD_PERCENTILE["wins"]) == 1
    assert _percentile(values_desc, METRIC_THRESHOLD_PERCENTILE["win_locations"]) == 1


def test_pick_home_max_wins() -> None:
    assert _pick_home({"a": 3, "b": 50, "c": 7}) == ("b", 50)


def test_pick_home_tie_is_deterministic() -> None:
    # При равенстве побед выбирается меньший ключ — снапшоты не «мигают».
    assert _pick_home({"z": 5, "a": 5, "m": 5}) == ("a", 5)


def test_pick_home_empty() -> None:
    assert _pick_home({}) is None


def test_pick_last_returns_most_recent_win() -> None:
    dates = {"a": date(2024, 5, 1), "b": date(2026, 7, 18), "c": date(2025, 1, 1)}
    assert _pick_last(dates) == ("b", date(2026, 7, 18))


def test_pick_last_tie_is_deterministic() -> None:
    # Две победы в один день на разных локациях — берём меньший ключ, чтобы
    # снапшот не «мигал» между пересчётами.
    same = date(2026, 7, 18)
    assert _pick_last({"z": same, "a": same, "m": same}) == ("a", same)


def test_pick_last_empty() -> None:
    assert _pick_last({}) is None


def test_apply_last_win_fills_name_and_slug() -> None:
    entity = _Entity(key="p:1")
    _apply_last_win(
        entity,
        {"catalog:1": date(2026, 1, 10), "catalog:2": date(2026, 7, 18)},
        {"catalog:1": "Первая", "catalog:2": "Вторая"},
        {"catalog:1": "pervaya", "catalog:2": "vtoraya"},
    )
    assert entity.last_win_location == "Вторая"
    assert entity.last_win_location_slug == "vtoraya"
    assert entity.last_win_date == date(2026, 7, 18)


def test_apply_last_win_without_slug_keeps_name() -> None:
    # Локация без внятного external_key: имя есть, ссылки не будет.
    entity = _Entity(key="p:1")
    _apply_last_win(entity, {"location:7": date(2026, 7, 18)}, {}, {})
    assert entity.last_win_location == "location:7"
    assert entity.last_win_location_slug is None


def test_win_extras_only_for_win_metrics() -> None:
    # Лучшее время и последняя победа — только у победных рейтингов.
    assert set(WIN_EXTRAS_METRICS) == {"wins", "win_locations"}
    assert set(WIN_EXTRAS_METRICS) <= set(LEADERBOARD_METRICS)


def test_dominant_gender_by_majority() -> None:
    assert _dominant_gender({"male": 30, "female": 2}) == "male"
    assert _dominant_gender({"female": 5}) == "female"
    assert _dominant_gender({}) is None


def test_metric_description_follows_gender() -> None:
    # В гендерных зачётах описание говорит про мужчин/женщин, не про абсолют.
    assert "абсолютном зачёте" in metric_description("wins", "all")
    assert "среди мужчин" in metric_description("wins", "male")
    assert "среди женщин" in metric_description("wins", "female")
    assert "среди мужчин" in metric_description("win_locations", "male")
    # У метрик без разреза по полу описание всегда базовое.
    assert metric_description("runs", "male") == METRIC_META["runs"]["description"]


def test_normalize_gender_only_for_win_metrics() -> None:
    # Пол применяется только к победным метрикам; у остальных всегда «all».
    assert set(GENDERED_METRICS) == {"wins", "win_locations"}
    assert _normalize_gender("wins", "male") == "male"
    assert _normalize_gender("win_locations", "female") == "female"
    assert _normalize_gender("runs", "male") == "all"
    assert _normalize_gender("wins", "нечто") == "all"


def test_normalize_min_visits_only_for_tourism_metrics() -> None:
    # Порог визитов есть у туристических рейтингов (бегового и волонтёрского)
    # и зажат в 1..5.
    assert set(MIN_VISITS_METRICS) == {"locations", "volunteer_locations"}
    assert _normalize_min_visits("locations", 3) == 3
    assert _normalize_min_visits("volunteer_locations", 3) == 3
    assert _normalize_min_visits("locations", 0) == 1
    assert _normalize_min_visits("locations", 99) == MAX_MIN_VISITS
    assert _normalize_min_visits("runs", 3) == 1
    assert _normalize_min_visits("win_locations", 4) == 1


def test_location_visits_threshold() -> None:
    # Локация даёт балл, только когда визитов набралось не меньше порога.
    visits = _LocationVisits(first_date=date(2026, 7, 4), visits=3, week_visits=0)
    assert visits.counts(3)
    assert not visits.counts(4)
    assert visits.counts(1)


def test_location_visits_new_when_threshold_crossed_this_week() -> None:
    # «Прибавилась на неделе» = норма набрана именно сейчас: до недели визитов
    # было меньше порога, с учётом недели — уже хватает.
    third_this_week = _LocationVisits(first_date=date(2026, 5, 2), visits=3, week_visits=1)
    assert third_this_week.counts(3) and third_this_week.is_new(3)
    # Та же локация при пороге «от 1» новой не считается: первый визит был давно.
    assert not third_this_week.is_new(1)
    # А четвёртый визит на этой неделе порога 3 уже не пересекает — не новая.
    fourth_this_week = _LocationVisits(first_date=date(2026, 5, 2), visits=4, week_visits=1)
    assert fourth_this_week.counts(3) and not fourth_this_week.is_new(3)


def test_merge_visit_row_sums_platforms_of_one_location() -> None:
    # Одна физическая площадка в двух системах — один зачёт, визиты суммируются.
    identities: dict[str, _LocationVisits] = {}
    _merge_visit_row(identities, "loc:1", "five_verst", date(2026, 3, 7), 2, 1)
    _merge_visit_row(identities, "loc:1", "s95", date(2026, 1, 10), 1, 0)
    merged = identities["loc:1"]
    assert merged.visits == 3
    assert merged.week_visits == 1
    assert merged.codes == {"five_verst", "s95"}
    assert merged.first_date == date(2026, 1, 10)
    assert merged.counts(3)


def test_cache_key_versions_min_visits() -> None:
    # Базовый вариант сохраняет прежний ключ, пороги — отдельными снапшотами.
    assert _cache_key("locations") == _cache_key("locations", "all", 1)
    assert _cache_key("locations", "all", 3).endswith(":locations:v3")
    assert _cache_key("wins", "male", 1).endswith(":wins:male")


def test_metric_description_mentions_min_visits() -> None:
    assert "минимум 3 раза" in metric_description("locations", "all", 3)
    assert "минимум 5 раз" in metric_description("locations", "all", 5)
    # Порог «от 1» — обычное описание рейтинга туризма.
    assert metric_description("locations", "all", 1) == METRIC_META["locations"]["description"]


def test_platform_filter_is_standard_for_every_metric() -> None:
    # Фильтр по системе — стандарт всех рейтингов, а не привилегия туризма.
    assert set(PLATFORM_FILTER_METRICS) == set(LEADERBOARD_METRICS)
    for metric in LEADERBOARD_METRICS:
        assert _normalize_platform_filter(metric, "five_verst") == "five_verst"
        assert _normalize_platform_filter(metric, "нечто") == "all"
    assert set(PLATFORM_FILTER_VALUES) == {"all", *PLATFORM_COLUMNS}


def test_platform_filter_values_follow_metric_and_gender() -> None:
    # parkrun есть в абсолюте, но не в гендерном зачёте и не в волонтёрском туризме.
    assert "parkrun" in platform_filter_values("wins", "all")
    assert "parkrun" not in platform_filter_values("wins", "male")
    assert "parkrun" not in platform_filter_values("volunteer_locations")
    assert platform_columns_for("volunteer_locations") == VOLUNTEER_LOCATION_PLATFORM_COLUMNS
    assert platform_columns_for("win_locations", "female") == GENDERED_PLATFORM_COLUMNS
    # Систему, которой в этом рейтинге нет, фильтр не принимает — молча «все».
    assert _normalize_platform_filter("wins", "parkrun", "male") == "all"
    assert _normalize_platform_filter("wins", "parkrun", "all") == "parkrun"
    assert _normalize_platform_filter("volunteer_locations", "parkrun") == "all"
    assert _normalize_platform_filter("volunteer_locations", "s95") == "s95"


def test_cache_key_versions_platform_and_combines_with_min_visits() -> None:
    assert _cache_key("locations") == _cache_key("locations", "all", 1, "all")
    assert _cache_key("locations", "all", 1, "s95").endswith(":locations:ps95")
    # Оба фильтра сразу — суффиксы идут в фиксированном порядке.
    assert _cache_key("locations", "all", 3, "s95").endswith(":locations:v3:ps95")


def test_metric_description_mentions_platform_filter() -> None:
    only_platform = metric_description("locations", "all", 1, "five_verst")
    assert "5 вёрст" in only_platform
    assert "минимум" not in only_platform
    combined = metric_description("locations", "all", 3, "parkrun")
    assert "минимум 3 раза" in combined
    assert "parkrun" in combined
    # Фильтр по системе не про пары систем — упоминание «суммируются» тут неуместно.
    assert "суммируются" not in only_platform


def test_metric_description_mentions_platform_filter_for_every_metric() -> None:
    # Фильтр по системе теперь у всех рейтингов — и описание про него у всех.
    for metric in LEADERBOARD_METRICS:
        assert "С95" in metric_description(metric, "all", 1, "s95")
    # Без фильтров описание остаётся базовым.
    assert metric_description("runs", "all") == METRIC_META["runs"]["description"]


def test_metric_description_uses_volunteer_verb_for_volunteer_tourism() -> None:
    # Порог визитов у волонтёрского туризма про смены, а не про финиши.
    volunteer = metric_description("volunteer_locations", "all", 3)
    assert "волонтёрил минимум 3 раза" in volunteer
    assert "финишировал" not in volunteer
    assert "финишировал минимум 3 раза" in metric_description("locations", "all", 3)


def test_volunteer_locations_registered_as_tourism_metric() -> None:
    # Волонтёрский туризм устроен как беговой: те же фильтры, тот же перцентиль,
    # только parkrun из него исключён (у его волонтёрств нет локации).
    assert "volunteer_locations" in LEADERBOARD_METRICS
    assert "volunteer_locations" in MIN_VISITS_METRICS
    assert (
        METRIC_THRESHOLD_PERCENTILE["volunteer_locations"]
        == METRIC_THRESHOLD_PERCENTILE["locations"]
    )
    assert "volunteer_locations" not in GENDERED_METRICS
    assert "volunteer_locations" not in WIN_EXTRAS_METRICS
