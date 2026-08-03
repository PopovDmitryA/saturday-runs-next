from datetime import date
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    RunResult,
)
from app.services.leaderboard_service import (
    _WEEK_LOCATIONS_PIDS_ALIAS,
    _WEEK_LOCATIONS_SQL_BY_METRIC,
    _WEEK_RUN_LOCATIONS_SQL,
    _WEEK_VOLUNTEER_LOCATIONS_SQL,
    COUNT_BY_METRICS,
    COUNT_BY_VALUES,
    GENDERED_METRICS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    METRIC_META,
    METRIC_THRESHOLD_PERCENTILE,
    MIN_VISITS_METRICS,
    PLATFORM_COLUMNS,
    PLATFORM_FILTER_METRICS,
    PLATFORM_FILTER_VALUES,
    VOLUNTEER_LOCATION_PLATFORM_COLUMNS,
    WEEK_LOCATIONS_LIMIT,
    WEEK_LOCATIONS_METRICS,
    WIN_EXTRAS_METRICS,
    _add_role_row,
    _apply_last_win,
    _cache_key,
    _dominant_gender,
    _Entity,
    _geo_keys,
    _LocationVisits,
    _merge_visit_row,
    _my_gendered_win_values,
    _my_win_values,
    _normalize_count_by,
    _normalize_gender,
    _normalize_min_visits,
    _normalize_platform_filter,
    _percentile,
    _pick_home,
    _pick_last,
    _ranked,
    _RoleUsage,
    _summarize_roles,
    _unit_counts,
    _unit_key_getters,
    _week_location_entries,
    _week_start,
    count_by_values,
    metric_description,
    metric_title,
    metric_unit,
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


def test_merge_visit_row_tracks_per_platform_visits() -> None:
    # Порог визитов в колонке системы должен набираться ЕЙ САМОЙ: 1 визит на
    # 5В + 1 визит на S95 даёт «Всего» 2+ (легитимно), но ни одна из систем
    # по отдельности порог 2 не набрала — это баг из отчёта про Глазкова
    # Александра (5В-колонка «в разрезе всех систем» была больше, чем при
    # прямом подсчёте только по 5В).
    identities: dict[str, _LocationVisits] = {}
    _merge_visit_row(identities, "loc:1", "five_verst", date(2026, 3, 7), 1, 0)
    _merge_visit_row(identities, "loc:1", "s95", date(2026, 1, 10), 1, 0)
    merged = identities["loc:1"]
    assert merged.counts(2)  # «Всего» видит эту локацию как 2+
    assert not merged.platform_counts("five_verst", 2)
    assert not merged.platform_counts("s95", 2)
    # А если один визит на 5В был дважды (два разных события) — сама 5В уже набрала порог.
    _merge_visit_row(identities, "loc:1", "five_verst", date(2026, 4, 4), 1, 0)
    assert merged.platform_counts("five_verst", 2)


def test_geo_keys_russia_uses_region_and_city() -> None:
    geo = _geo_keys("Россия", "Московская область", "Балашиха")
    assert geo.region == "московская область"
    # Город ключуется парой «регион + город»: одноимённые города разных регионов
    # (Троицк в Москве и в Челябинской области) — это два разных города.
    assert geo.city == "московская область|балашиха"
    other = _geo_keys("Россия", "Челябинская область", "Троицк")
    moscow = _geo_keys("Россия", "Москва", "Троицк")
    assert other.city != moscow.city


def test_geo_keys_foreign_country_is_one_region() -> None:
    # «1 страна = 1 регион» (решение Дмитрия 02.08.2026): все британские
    # parkrun-площадки дают ровно один регион, сколько бы их ни было.
    london = _geo_keys("Великобритания", None, None)
    bushy = _geo_keys("Великобритания", None, None)
    assert london.region == bushy.region == "country:великобритания"
    # Города за границей почти всегда неизвестны — тогда страна идёт и за город,
    # чтобы столбец не обнулялся на зарубежных поездках.
    assert london.city == "country:великобритания"
    # А если город всё-таки известен — считаем именно его.
    tbilisi = _geo_keys("Грузия", None, "Тбилиси")
    assert tbilisi.city == "грузия|тбилиси"
    assert tbilisi.region == "country:грузия"


def test_geo_keys_fold_country_spellings() -> None:
    # На проде британские parkrun-площадки записаны двумя способами
    # («Великобритания» и «United Kingdom») — это одна страна и один регион,
    # иначе турист по паркранам Британии получал бы два региона вместо одного.
    assert _geo_keys("United Kingdom", None, None).region == _geo_keys(
        "Великобритания", None, None
    ).region


def test_geo_keys_without_data_drop_out_of_geo_ratings() -> None:
    # Ни страны, ни региона, ни города — площадка не идёт ни в зачёт городов,
    # ни в зачёт регионов (бакет «неизвестно» был бы враньём).
    empty = _geo_keys(None, None, None)
    assert empty.city is None and empty.region is None
    # Русская площадка без региона всё же даёт город — по имени города.
    assert _geo_keys("Россия", None, "Курск").city == "|курск"


def _visits(*, visits: int, week_visits: int, code: str = "five_verst") -> _LocationVisits:
    row = _LocationVisits(
        first_date=date(2026, 1, 3), codes={code}, visits=visits, week_visits=week_visits
    )
    row.by_platform[code] = [visits, week_visits]
    return row


def test_unit_counts_locations_counts_every_venue() -> None:
    # Единица «площадки» — это ровно прежний построчный подсчёт.
    counted = {
        "loc:1": _visits(visits=2, week_visits=0),
        "loc:2": _visits(visits=1, week_visits=1),
    }
    tally = _unit_counts(counted, lambda identity: identity, 1)
    assert tally.total == 2
    assert tally.week == 1
    assert tally.values["five_verst"] == [2, 1]


def test_unit_counts_collapses_venues_of_one_city() -> None:
    # Два парка одного города дают ОДИН город, а не два.
    counted = {
        "loc:1": _visits(visits=3, week_visits=0),
        "loc:2": _visits(visits=1, week_visits=1),
        "loc:3": _visits(visits=1, week_visits=1),
    }
    geo = {
        "loc:1": _geo_keys("Россия", "Москва", "Москва"),
        "loc:2": _geo_keys("Россия", "Москва", "Москва"),
        "loc:3": _geo_keys("Россия", "Курская область", "Курск"),
    }
    cities = _unit_counts(counted, _unit_key_getters(geo)["cities"], 1)
    assert cities.total == 2
    # Москва не «прибавилась»: один из её парков был освоен ещё до недели.
    # Курск — целиком новый город.
    assert cities.week == 1
    regions = _unit_counts(counted, _unit_key_getters(geo)["regions"], 1)
    assert regions.total == 2


def test_unit_counts_skips_venues_without_geo() -> None:
    # Площадка без города в зачёт городов не идёт, но в зачёте площадок остаётся.
    counted = {"loc:1": _visits(visits=1, week_visits=0)}
    geo = {"loc:1": _geo_keys(None, None, None)}
    assert _unit_counts(counted, _unit_key_getters(geo)["cities"], 1).total == 0
    assert _unit_counts(counted, lambda identity: identity, 1).total == 1


def test_normalize_count_by_only_for_tourism_metrics() -> None:
    assert _normalize_count_by("locations", "cities") == "cities"
    assert _normalize_count_by("volunteer_locations", "regions") == "regions"
    # У остальных рейтингов гео-зачёта нет — молча откатываем к площадкам.
    assert _normalize_count_by("runs", "cities") == "locations"
    assert _normalize_count_by("win_locations", "regions") == "locations"
    assert _normalize_count_by("locations", "мусор") == "locations"


def test_count_by_options_offered_only_where_supported() -> None:
    for metric in LEADERBOARD_METRICS:
        options = count_by_values(metric)
        if metric in COUNT_BY_METRICS:
            assert options == COUNT_BY_VALUES
        else:
            assert options == ()
    # Гео-зачёт живёт ровно там же, где порог визитов, — у туризма.
    assert COUNT_BY_METRICS == MIN_VISITS_METRICS


def test_metric_title_and_unit_follow_count_by() -> None:
    assert "города" in metric_title("locations", "cities")
    assert "регионы" in metric_title("volunteer_locations", "regions")
    assert metric_title("locations", "locations") == METRIC_META["locations"]["title"]
    assert metric_unit("locations", "cities") == "городов"
    assert metric_unit("locations", "regions") == "регионов"
    assert metric_unit("runs", "cities") == METRIC_META["runs"]["unit"]


def test_metric_description_mentions_count_by() -> None:
    cities = metric_description("locations", "all", 1, "all", "cities")
    assert "ГОРОДА" in cities
    assert "Зарубежные старты считаются по стране" in cities
    # Гео-зачёт комбинируется с порогом визитов, не вытесняя его.
    combined = metric_description("locations", "all", 3, "all", "regions")
    assert "РЕГИОНЫ" in combined and "минимум 3 раза" in combined


def test_cache_key_versions_count_by() -> None:
    # Базовый вариант (площадки) сохраняет прежний ключ.
    assert _cache_key("locations") == _cache_key("locations", "all", 1, "all", "locations")
    assert _cache_key("locations", "all", 1, "all", "cities").endswith(":locations:ccities")
    # Единица зачёта комбинируется с порогом визитов и системой.
    assert _cache_key("locations", "all", 3, "s95", "regions").endswith(
        ":locations:v3:ps95:cregions"
    )


def test_week_locations_metrics_registered() -> None:
    # Колонка «Последняя неделя» — у пробежек, волонтёрств, обоих туристических
    # рейтингов и дальности от дома; у победных её место занимает «Последняя
    # победа».
    assert set(WEEK_LOCATIONS_METRICS) == {
        "runs",
        "volunteering",
        "locations",
        "volunteer_locations",
        "home_distance",
    }
    assert set(WEEK_LOCATIONS_METRICS) <= set(LEADERBOARD_METRICS)
    assert set(WEEK_LOCATIONS_METRICS) & set(WIN_EXTRAS_METRICS) == set()
    assert "volunteer_roles" not in WEEK_LOCATIONS_METRICS


def test_week_locations_read_the_metrics_own_protocols() -> None:
    # Беговые рейтинги берут окно из протоколов забегов, волонтёрские — из
    # волонтёрских смен: иначе в «Последней неделе» туризма оказались бы смены,
    # а в волонтёрском туризме — пробежки.
    assert _WEEK_LOCATIONS_SQL_BY_METRIC["locations"] is _WEEK_RUN_LOCATIONS_SQL
    assert _WEEK_LOCATIONS_SQL_BY_METRIC["runs"] is _WEEK_RUN_LOCATIONS_SQL
    assert _WEEK_LOCATIONS_SQL_BY_METRIC["home_distance"] is _WEEK_RUN_LOCATIONS_SQL
    assert (
        _WEEK_LOCATIONS_SQL_BY_METRIC["volunteer_locations"]
        is _WEEK_VOLUNTEER_LOCATIONS_SQL
    )
    assert _WEEK_LOCATIONS_SQL_BY_METRIC["volunteering"] is _WEEK_VOLUNTEER_LOCATIONS_SQL
    # Фильтр участников «моей» строки должен ссылаться на таблицу этой выборки.
    for metric, alias in _WEEK_LOCATIONS_PIDS_ALIAS.items():
        assert f"{alias}.participant_id IS NOT NULL" in _WEEK_LOCATIONS_SQL_BY_METRIC[metric]


def test_week_location_entries_order_and_limit() -> None:
    names = {f"loc:{i}": f"Площадка {i}" for i in range(1, 8)}
    slugs = {"loc:1": "park-one"}
    dates = {f"loc:{i}": date(2026, 7, 19 + (i % 3)) for i in range(1, 8)}
    entries = _week_location_entries(dates, names, slugs)
    # Свежие первыми, длинный хвост обрезан — его фронт сворачивает в «+N».
    assert len(entries) == WEEK_LOCATIONS_LIMIT
    assert entries[0]["date"] >= entries[-1]["date"]
    # При равной дате порядок детерминирован по названию, слаг подставляется.
    same_day = _week_location_entries(
        {"loc:2": date(2026, 7, 25), "loc:1": date(2026, 7, 25)}, names, slugs
    )
    assert [item["name"] for item in same_day] == ["Площадка 1", "Площадка 2"]
    assert same_day[0]["slug"] == "park-one"
    assert same_day[0]["date"] == "2026-07-25"


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


def test_platform_filter_values_follow_metric() -> None:
    # parkrun есть везде, кроме волонтёрского туризма, — в том числе в гендерном
    # зачёте побед (до 02.08.2026 он оттуда вырезался).
    assert "parkrun" in platform_filter_values("wins")
    assert "parkrun" not in platform_filter_values("volunteer_locations")
    assert platform_columns_for("volunteer_locations") == VOLUNTEER_LOCATION_PLATFORM_COLUMNS
    assert platform_columns_for("win_locations") == PLATFORM_COLUMNS
    # Систему, которой в этом рейтинге нет, фильтр не принимает — молча «все».
    assert _normalize_platform_filter("wins", "parkrun") == "parkrun"
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


def test_volunteer_roles_registered_as_plain_metric() -> None:
    # Мультиволонтёр — обычный рейтинг: без порога визитов, без разреза М/Ж,
    # с общим для всех фильтром по системе (parkrun в нём участвует: роли
    # приходят сводкой профиля).
    assert "volunteer_roles" in LEADERBOARD_METRICS
    assert "volunteer_roles" not in MIN_VISITS_METRICS
    assert "volunteer_roles" not in GENDERED_METRICS
    assert "volunteer_roles" not in WIN_EXTRAS_METRICS
    assert "parkrun" in platform_filter_values("volunteer_roles")


def test_summarize_roles_counts_union_not_sum() -> None:
    # Одна и та же роль в двух системах — одна освоенная роль, но в колонке
    # каждой системы она видна.
    week_start = date(2026, 7, 27)
    long_ago = date(2024, 1, 6)
    by_platform = {
        "five_verst": {
            "marshal": _RoleUsage(first_date=long_ago, times=10),
            "timekeeper": _RoleUsage(first_date=long_ago, times=3),
        },
        "s95": {"marshal": _RoleUsage(first_date=date(2025, 5, 3), times=4)},
    }
    labels = {"marshal": "Маршал", "timekeeper": "Секундомер"}
    summary = _summarize_roles(by_platform, labels, week_start)
    assert summary.total == 2
    assert summary.values["five_verst"] == [2, 0]
    assert summary.values["s95"] == [1, 0]
    assert summary.week == 0
    # Любимая роль — по сумме смен во всех системах (10 + 4 против 3).
    assert summary.top_role == ("Маршал", 14)
    # Детализация показывает, из каких систем собралась каждая роль.
    assert summary.details == [
        {"role": "Маршал", "total": 14, "platforms": {"five_verst": 10, "s95": 4}},
        {"role": "Секундомер", "total": 3, "platforms": {"five_verst": 3}},
    ]


def test_summarize_roles_week_delta_counts_only_first_time_ever() -> None:
    # Роль «новая», если ПЕРВАЯ смена в ней случилась на этой неделе — освоенное
    # год назад в другой системе повторение новой ролью не делает.
    week_start = date(2026, 7, 27)
    by_platform = {
        "five_verst": {"marshal": _RoleUsage(first_date=date(2024, 1, 6), times=9)},
        "s95": {
            "marshal": _RoleUsage(first_date=date(2026, 7, 31), times=1),
            "pacer": _RoleUsage(first_date=date(2026, 7, 31), times=1),
        },
    }
    labels = {"marshal": "Маршал", "pacer": "Пейсмейкер"}
    summary = _summarize_roles(by_platform, labels, week_start)
    assert summary.total == 2
    assert summary.week == 1


def test_add_role_row_folds_system_synonyms_into_one_role() -> None:
    # Внутри одной системы разные ярлыки одной роли («Сканер» и веха «Сканер 25»)
    # складываются, а не затирают друг друга.
    by_platform: dict[str, dict[str, _RoleUsage]] = {}
    labels: dict[str, str] = {}
    _add_role_row(by_platform, labels, "s95", "Сканер", date(2025, 3, 1), 20)
    _add_role_row(by_platform, labels, "s95", "Сканер 25", date(2024, 9, 7), 1)
    assert list(by_platform["s95"]) == ["barcode_scanning"]
    usage = by_platform["s95"]["barcode_scanning"]
    assert usage.times == 21
    assert usage.first_date == date(2024, 9, 7)


def test_add_role_row_uses_parkrun_credits_as_shift_count() -> None:
    # У parkrun отдельных смен нет — только сводка «роль × кредитов», и число
    # смен берётся из самого ярлыка, а не из числа строк.
    by_platform: dict[str, dict[str, _RoleUsage]] = {}
    labels: dict[str, str] = {}
    _add_role_row(by_platform, labels, "parkrun", "Marshal (12×)", date(1970, 1, 1), 1)
    assert by_platform["parkrun"]["marshal"].times == 12


def test_add_role_row_skips_only_parkrun_summary_total() -> None:
    # «Total Credits» — итог сводки профиля, а не роль. «Разное» — роль как
    # любая другая (решение Дмитрия 01.08.2026), в зачёт идёт.
    by_platform: dict[str, dict[str, _RoleUsage]] = {}
    labels: dict[str, str] = {}
    _add_role_row(by_platform, labels, "parkrun", "Total Credits (115×)", date(1970, 1, 1), 1)
    assert by_platform == {}
    _add_role_row(by_platform, labels, "five_verst", "Разное", date(2026, 1, 3), 5)
    assert list(by_platform["five_verst"]) == ["other"]
    assert labels["other"] == "Разное"


def _seed_parkrun_wins_for_rating(
    db_session: Session, *, catalogued: list[bool], gender: str | None = None
) -> UUID:
    """Один parkrun-участник с первым местом на каждой из площадок.

    catalogued задаёт по площадке: True — русская (есть связка с каталогом
    локаций), False — зарубежная. Половина русских стартов заодно делает
    участника «допущенным» до рейтингов (см. _PARKRUN_ELIGIBLE_CTE), поэтому
    зарубежная строка отсеивается именно правилом площадки, а не допуском.

    gender (если задан) проставляет participants.gender — источник пола для
    parkrun — и gender_position = 1 на ВСЕХ строках, включая зарубежные: так
    гендерный зачёт проверяется на явный фильтр площадки, а не на то, что у
    зарубежных стартов gender_position и так обычно NULL.
    """
    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        platform = Platform(code="parkrun", name="parkrun")
        db_session.add(platform)
        db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"parkrun-rating-user-{suffix}",
        display_name="Rating Tester",
        profile_url=f"https://www.parkrun.com/parkrunner/{suffix}/",
        gender=gender,
    )
    db_session.add(participant)
    db_session.flush()

    for index, is_russian in enumerate(catalogued):
        location = Location(
            platform_id=platform.id,
            external_key=f"parkrun-rating-{suffix}-{index}",
            name="Parkrun Rating Park",
            country="United Kingdom",
        )
        db_session.add(location)
        db_session.flush()

        if is_russian:
            catalog = LocationCatalog(
                canonical_name=f"Parkrun Rating Park {suffix}-{index}",
                active_platform="five_verst",
                is_closed=False,
            )
            db_session.add(catalog)
            db_session.flush()
            db_session.add(
                LocationCatalogLink(
                    catalog_id=catalog.id,
                    platform_id=platform.id,
                    external_key=location.external_key,
                    location_id=location.id,
                )
            )

        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"parkrun-rating-event-{suffix}-{index}",
            event_date=date(2019, 6, 1 + index),
            event_number=100 + index,
            title="Parkrun Rating Event",
            finishers_count=1,
            runners_count=1,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"parkrun-rating-result-{suffix}-{index}",
                position=1,
                gender_position=1 if gender else None,
                finish_time_sec=22 * 60,
                finish_time_display="00:22:00",
                status="finished",
            )
        )
    db_session.flush()
    return participant.id


def test_win_rating_counts_only_russian_parkrun(db_session: Session) -> None:
    """Рейтинг побед считает parkrun только на русских площадках: зарубежный
    протокол мы не видим, и одинокая строка из профиля всегда первая."""
    participant_id = _seed_parkrun_wins_for_rating(db_session, catalogued=[True, False])
    values, _home, _last = _my_win_values(db_session, [participant_id], date(2026, 7, 27))
    assert values["parkrun"][0] == 1


def test_win_rating_skips_parkrun_without_russian_starts(db_session: Session) -> None:
    participant_id = _seed_parkrun_wins_for_rating(db_session, catalogued=[False, False])
    values, _home, _last = _my_win_values(db_session, [participant_id], date(2026, 7, 27))
    assert values == {}


def test_gendered_win_rating_includes_parkrun(db_session: Session) -> None:
    """С 02.08.2026 parkrun входит в разбивку по полу: пол берётся из
    participants.gender (в run_results.age_category у parkrun лежит age-grade %)."""
    participant_id = _seed_parkrun_wins_for_rating(
        db_session, catalogued=[True, False], gender="male"
    )
    values, total, _week, _home, _last = _my_gendered_win_values(
        db_session, [participant_id], date(2026, 7, 27), "male", as_locations=False
    )
    # Зачтена только русская площадка — зарубежная отсечена, хотя gender_position там тоже 1.
    assert values["parkrun"][0] == 1
    assert total == 1


def test_gendered_win_rating_skips_foreign_parkrun(db_session: Session) -> None:
    participant_id = _seed_parkrun_wins_for_rating(
        db_session, catalogued=[False, False], gender="female"
    )
    values, total, _week, _home, _last = _my_gendered_win_values(
        db_session, [participant_id], date(2026, 7, 27), "female", as_locations=False
    )
    assert values == {}
    assert total == 0


def test_gendered_win_rating_excludes_other_gender(db_session: Session) -> None:
    # Мужчина в женском зачёте не появляется, даже с первыми местами среди мужчин.
    participant_id = _seed_parkrun_wins_for_rating(
        db_session, catalogued=[True, True], gender="male"
    )
    values, total, _week, _home, _last = _my_gendered_win_values(
        db_session, [participant_id], date(2026, 7, 27), "female", as_locations=False
    )
    assert values == {}
    assert total == 0
