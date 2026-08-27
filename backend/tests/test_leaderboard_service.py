from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    RunResult,
    VolunteerResult,
)
from app.services.leaderboard_service import (
    _LOCATION_VISITS_SQL,
    _METRIC_PIDS_ALIAS,
    _TOURIST_MAP_SQL,
    _TOURIST_RUN_VISITS_SQL,
    _TOURIST_VOLUNTEER_VISITS_SQL,
    _VOLUNTEER_LOCATION_ROLE_ROWS_SQL,
    _VOLUNTEER_LOCATION_VISITS_SQL,
    _WEEK_LOCATIONS_SQL_BY_METRIC,
    _WEEK_RUN_LOCATIONS_SQL,
    _WEEK_VOLUNTEER_LOCATIONS_SQL,
    COUNT_BY_METRICS,
    COUNT_BY_VALUES,
    FORECAST_LIVE_PLATFORMS,
    FORECAST_METRICS,
    GENDERED_METRICS,
    LEADERBOARD_GENDERS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    METRIC_META,
    METRIC_THRESHOLD_PERCENTILE,
    MIN_VISITS_METRICS,
    PLATFORM_COLUMNS,
    PLATFORM_FILTER_METRICS,
    PLATFORM_FILTER_VALUES,
    TOP_LIMIT,
    TOURIST_MAP_LIMIT,
    TOURIST_MAP_METRICS,
    TOURIST_MAP_TOP_STEPS,
    VOLUNTEER_LOCATION_PLATFORM_COLUMNS,
    WEEK_LOCATIONS_METRICS,
    WIN_EXTRAS_METRICS,
    _add_role_row,
    _apply_last_win,
    _cache_key,
    _dominant_gender,
    _Entity,
    _entity_key,
    _geo_keys,
    _latest_week_location,
    _LocationGeo,
    _LocationVisits,
    _merge_visit_row,
    _my_gendered_win_values,
    _my_location_values,
    _my_week_location,
    _my_win_values,
    _normalize_count_by,
    _normalize_gender,
    _normalize_min_visits,
    _normalize_platform_filter,
    _OpenLocations,
    _percentile,
    _pick_home,
    _pick_last,
    _ranked,
    _remaining_units,
    _RoleUsage,
    _row_key,
    _SiteLink,
    _summarize_roles,
    _tourist_pids_filter,
    _tourist_row_participants,
    _tourist_visit_payload,
    _TouristPlatformVisit,
    _unit_counts,
    _unit_key_getters,
    _week_start,
    count_by_values,
    forecast_available,
    forecast_finish_date,
    metric_description,
    start_schedule,
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
    # В женском зачёте описание говорит про женщин, не про абсолют.
    assert "абсолютном зачёте" in metric_description("wins", "all")
    assert "среди женщин" in metric_description("wins", "female")
    assert "среди женщин" in metric_description("win_locations", "female")
    # У метрик без разреза по полу описание всегда базовое.
    assert metric_description("runs", "female") == METRIC_META["runs"]["description"]


def test_normalize_gender_only_for_win_metrics() -> None:
    # Пол применяется только к победным метрикам; у остальных всегда «all».
    assert set(GENDERED_METRICS) == {"wins", "win_locations"}
    assert _normalize_gender("win_locations", "female") == "female"
    assert _normalize_gender("runs", "female") == "all"
    assert _normalize_gender("wins", "нечто") == "all"


def test_male_gender_scope_is_gone() -> None:
    # Мужского зачёта нет: первые места среди мужчин завышены на стартах, где
    # у части финишёров протокол не даёт пола (нет возрастной категории).
    assert set(LEADERBOARD_GENDERS) == {"all", "female"}
    # Старые ссылки с ?gender=male не 404-ят и не считают мужской зачёт —
    # молча открывают абсолют.
    assert _normalize_gender("wins", "male") == "all"
    assert _normalize_gender("win_locations", "male") == "all"
    assert "мужчин" not in metric_description("wins", "male")


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
    # Прибавку дала ровно одна площадка — её и запоминаем для «Последней недели».
    assert tally.new_identities == {"loc:2"}


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
    # «Прибавился» Курск — значит и площадка недели должна быть курская, даже
    # если позже человек сходил в давно освоенный московский парк.
    assert cities.new_identities == {"loc:3"}
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
    for metric, alias in _METRIC_PIDS_ALIAS.items():
        assert f"{alias}.participant_id IS NOT NULL" in _WEEK_LOCATIONS_SQL_BY_METRIC[metric]


def test_latest_week_location_takes_the_freshest_start() -> None:
    """В ячейке всегда одна площадка — самый поздний старт окна."""
    names = {f"loc:{i}": f"Площадка {i}" for i in range(1, 8)}
    slugs = {"loc:1": "park-one", "loc:5": "park-five"}
    dates = {
        "loc:3": date(2026, 7, 19),
        "loc:5": date(2026, 7, 26),
        "loc:7": date(2026, 7, 22),
    }
    latest = _latest_week_location(dates, names, slugs)
    assert latest == {"name": "Площадка 5", "slug": "park-five", "date": "2026-07-26"}

    # При равной дате выбор детерминирован по названию, слаг подставляется.
    same_day = _latest_week_location(
        {"loc:2": date(2026, 7, 25), "loc:1": date(2026, 7, 25)}, names, slugs
    )
    assert same_day == {"name": "Площадка 1", "slug": "park-one", "date": "2026-07-25"}

    # Не был нигде — ячейка пустая.
    assert _latest_week_location({}, names, slugs) is None


def test_latest_week_location_prefers_the_venue_that_gave_the_delta() -> None:
    """В один день можно отволонтёрить дважды: рядом с «+1» — новая площадка.

    Репорт Дмитрия 22.08.2026: в рейтинге волонтёрского туризма дельта была +1,
    а в «Последней неделе» стоял повтор — он просто оказался позже по алфавиту
    при равной дате.
    """
    names = {"loc:new": "Новая площадка", "loc:again": "Мещерский"}
    slugs = {"loc:new": "new-park", "loc:again": "meshchersky"}
    same_day = {"loc:new": date(2026, 8, 22), "loc:again": date(2026, 8, 22)}

    # Без подсказки побеждает алфавит — прежнее поведение.
    assert _latest_week_location(same_day, names, slugs)["name"] == "Мещерский"
    # С подсказкой — та площадка, которая и дала прибавку.
    assert _latest_week_location(same_day, names, slugs, {"loc:new"}) == {
        "name": "Новая площадка",
        "slug": "new-park",
        "date": "2026-08-22",
    }

    # Даже если повтор был ПОЗЖЕ новой площадки, в ячейке всё равно новая.
    later_repeat = {"loc:new": date(2026, 8, 19), "loc:again": date(2026, 8, 22)}
    assert _latest_week_location(later_repeat, names, slugs, {"loc:new"}) == {
        "name": "Новая площадка",
        "slug": "new-park",
        "date": "2026-08-19",
    }

    # Прибавки не было — выбираем как раньше, по самому позднему визиту.
    assert _latest_week_location(later_repeat, names, slugs, set())["name"] == "Мещерский"
    # Площадка прибавки вне окна (так не бывает, но пусть не роняет ячейку).
    assert _latest_week_location(later_repeat, names, slugs, {"loc:other"})["name"] == "Мещерский"


def test_cache_key_versions_min_visits() -> None:
    # Базовый вариант сохраняет прежний ключ, пороги — отдельными снапшотами.
    assert _cache_key("locations") == _cache_key("locations", "all", 1)
    assert _cache_key("locations", "all", 3).endswith(":locations:v3")
    assert _cache_key("wins", "female", 1).endswith(":wins:female")


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
        db_session, catalogued=[True, False], gender="female"
    )
    values, total, _week, _home, _last = _my_gendered_win_values(
        db_session, [participant_id], date(2026, 7, 27), "female", as_locations=False
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


# ─── Карта туристов ──────────────────────────────────────────────────────────


def test_row_key_is_stable_and_hides_identifiers() -> None:
    """Якорь строки стабилен между пересчётами и не выдаёт внутренний ключ."""
    user_id = uuid4()
    key = _entity_key(user_id, _SiteLink(user_id=user_id, serial_id=7, display_name=None, private=False))
    assert _row_key(key) == _row_key(key)
    assert _row_key(key) != _row_key(f"p:{user_id}")
    assert str(user_id) not in _row_key(key)


def test_tourist_map_queries_keep_the_pids_placeholder() -> None:
    """Точечные выборки карты обязаны уметь фильтр по участникам.

    Без плейсхолдера replace() тихо ничего не заменит, и вместо сотни строк
    рейтинга запрос уйдёт сканировать протоколы целиком.
    """
    for metric, sql in _TOURIST_MAP_SQL.items():
        assert "/*PIDS_FILTER*/" in sql
        assert _tourist_pids_filter(metric) in sql.replace(
            "/*PIDS_FILTER*/", _tourist_pids_filter(metric)
        )
    assert "/*PIDS_FILTER*/" in _VOLUNTEER_LOCATION_ROLE_ROWS_SQL
    assert _tourist_pids_filter("locations") == "AND rr.participant_id = ANY(:pids)"
    assert _tourist_pids_filter("volunteer_locations") == "AND vr.participant_id = ANY(:pids)"


def test_tourist_map_sql_adds_last_date_without_touching_the_rating() -> None:
    """Дата последнего визита — только у выборок карты: рейтинг распаковывает
    шесть полей, и седьмое в его выборке сломало бы все распаковки."""
    assert "MAX(e.event_date) AS last_date" in _TOURIST_RUN_VISITS_SQL
    assert "MAX(e.event_date) AS last_date" in _TOURIST_VOLUNTEER_VISITS_SQL
    assert "last_date" not in _LOCATION_VISITS_SQL
    assert "last_date" not in _VOLUNTEER_LOCATION_VISITS_SQL


def test_tourist_visit_payload_merges_systems() -> None:
    """Светофор — про физическую площадку: визиты систем складываются, а даты
    берутся крайние по всем системам сразу."""
    platforms = {
        "parkrun": _TouristPlatformVisit(
            visits=2, first_date=date(2017, 4, 22), last_date=date(2017, 9, 9)
        ),
        "five_verst": _TouristPlatformVisit(
            visits=5, first_date=date(2023, 1, 21), last_date=date(2026, 7, 25)
        ),
    }
    payload = _tourist_visit_payload("abc123", platforms)
    assert payload["visits"] == 7
    assert payload["first_date"] == "2017-04-22"
    assert payload["last_date"] == "2026-07-25"
    # Порядок систем — как в колонках рейтинга: активные, затем архивный parkrun.
    assert [item["code"] for item in payload["platforms"]] == ["five_verst", "parkrun"]


def test_tourist_visit_payload_ignores_the_visits_threshold() -> None:
    """Светофор отвечает «был или не был», а не «засчитано ли» (решение Дмитрия
    15.08.2026): единственный визит — такой же зелёный, как двадцатый, и порог
    рейтинга на него не влияет. Сколько раз человек там был, видно в подсказке."""
    platforms = {
        "five_verst": _TouristPlatformVisit(
            visits=1, first_date=date(2026, 1, 10), last_date=date(2026, 1, 10)
        )
    }
    payload = _tourist_visit_payload("abc123", platforms)
    assert payload["visits"] == 1
    assert "counted" not in payload


def test_tourist_row_participants_expands_site_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Строка зарегистрированного собрана из всех его платформ — карта должна
    перечитать протоколы каждой, иначе половина визитов пропадёт."""
    user_id = uuid4()
    first, second, lone = uuid4(), uuid4(), uuid4()
    link = _SiteLink(user_id=user_id, serial_id=42, display_name="Бегун", private=False)
    monkeypatch.setattr(
        "app.services.leaderboard_service._site_links",
        lambda _db: {first: link, second: link},
    )
    mapping = _tourist_row_participants(None, [f"u:{user_id}", f"p:{lone}", "мусор"])
    assert mapping == {
        first: _row_key(f"u:{user_id}"),
        second: _row_key(f"u:{user_id}"),
        lone: _row_key(f"p:{lone}"),
    }


def test_tourist_map_covers_every_visible_row() -> None:
    """Глубина карты равна глубине таблицы (решение Дмитрия 15.08.2026).

    Иначе у строк ниже расчёта стоял бы прочерк, который пришлось бы объяснять
    подсказкой: светофор должен быть у каждой строки, которую видно.
    """
    assert TOURIST_MAP_LIMIT == TOP_LIMIT
    assert set(TOURIST_MAP_METRICS) == set(MIN_VISITS_METRICS)


def test_tourist_map_top_steps_end_at_table_depth() -> None:
    """Ступени фильтра карты идут по возрастанию и упираются в глубину таблицы.

    Самая широкая ступень обязана совпадать с TOURIST_MAP_LIMIT: витрина берёт
    её как значение по умолчанию, и карта открывается такой же, какой была до
    появления фильтра. Ступень шире расчёта нарисовала бы числа, которых нет.
    """
    assert list(TOURIST_MAP_TOP_STEPS) == sorted(TOURIST_MAP_TOP_STEPS)
    assert len(set(TOURIST_MAP_TOP_STEPS)) == len(TOURIST_MAP_TOP_STEPS)
    assert TOURIST_MAP_TOP_STEPS[0] > 0
    assert TOURIST_MAP_TOP_STEPS[-1] == TOURIST_MAP_LIMIT


def _seed_volunteer_week(db_session: Session) -> tuple[UUID, str, str]:
    """Волонтёр: давняя площадка «Мещерский» и новая — обе в одну субботу.

    Ровно случай из репорта: за неделю два волонтёрства в один день, «+1» дала
    только новая площадка, повтор — нет.
    """
    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"vol-week-user-{suffix}",
        display_name="Volunteer Tester",
    )
    db_session.add(participant)
    db_session.flush()

    def venue(tag: str, name: str) -> Location:
        location = Location(
            platform_id=platform.id,
            external_key=f"vol-week-{suffix}-{tag}",
            name=name,
            country="Россия",
        )
        db_session.add(location)
        db_session.flush()
        return location

    def shift(location: Location, on_date: date, tag: str) -> None:
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"vol-week-event-{suffix}-{tag}",
            event_date=on_date,
            event_number=1,
            title="Volunteer Week Event",
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"vol-week-result-{suffix}-{tag}",
                role="Маршал",
            )
        )

    # Новая площадка нарочно позже по алфавиту: при равной дате прежний выбор
    # взял бы повтор, и тест поймал бы регресс.
    repeat = venue("repeat", f"Мещерский {suffix}")
    fresh = venue("fresh", f"Яуза {suffix}")
    # Повтор освоен задолго до окна недели, новая площадка — впервые в субботу.
    shift(repeat, date(2026, 6, 6), "repeat-old")
    shift(repeat, date(2026, 8, 22), "repeat-week")
    shift(fresh, date(2026, 8, 22), "fresh-week")
    db_session.flush()
    return participant.id, repeat.name, fresh.name


def test_my_week_location_shows_the_venue_behind_the_plus_one(db_session: Session) -> None:
    """«Последняя неделя» показывает площадку, давшую «+1», а не повтор."""
    participant_id, repeat_name, fresh_name = _seed_volunteer_week(db_session)
    week_start = date(2026, 8, 16)

    row = _my_location_values(
        db_session,
        [participant_id],
        week_start,
        sql_template=_VOLUNTEER_LOCATION_VISITS_SQL,
        with_geo=True,
    )
    assert row.week == 1, "прибавка недели — ровно одна новая площадка"

    cell = _my_week_location(
        db_session,
        "volunteer_locations",
        [participant_id],
        week_start,
        prefer=row.new_identities,
    )
    assert cell is not None
    assert cell["name"] == fresh_name

    # Без подсказки — прежнее поведение: обе смены в одну субботу, ничью
    # разводит алфавит, и в ячейке оказывается повтор. Ровно то, на что
    # пожаловался Дмитрий.
    plain = _my_week_location(
        db_session, "volunteer_locations", [participant_id], week_start
    )
    assert plain is not None
    assert plain["name"] == repeat_name


# ─── Прогноз завершения туризма ──────────────────────────────────────────────
# Перенос дашборда Grafana «Прогноз даты завершения туризма»: колонки «Осталось»
# и «Прогноз» в рейтинге туризма.


def test_start_schedule_walks_saturdays() -> None:
    # 22.08.2026 — суббота: следующие возможности взять площадку это следующие
    # субботы, сама отбеганная суббота в расписание не входит.
    assert start_schedule(date(2026, 8, 22), 3) == [
        date(2026, 8, 29),
        date(2026, 9, 5),
        date(2026, 9, 12),
    ]


def test_start_schedule_counts_new_year_twice() -> None:
    # 1 января стартов два (в этот день реально закрыть две площадки), 12 июня —
    # один. 01.01.2027 — пятница, обычной субботой он не был бы вовсе.
    slots = start_schedule(date(2026, 12, 26), 4)
    assert slots == [
        date(2027, 1, 1),
        date(2027, 1, 1),
        date(2027, 1, 2),
        date(2027, 1, 9),
    ]


def test_start_schedule_bonus_on_saturday_is_not_doubled() -> None:
    # 12.06.2027 — суббота: бонусный день не добавляет к своему старту ещё и
    # субботний, иначе один день дал бы две площадки на ровном месте.
    slots = start_schedule(date(2027, 6, 5), 3)
    assert slots == [date(2027, 6, 12), date(2027, 6, 19), date(2027, 6, 26)]
    # 01.01.2028 — суббота, и там стартов по-прежнему два.
    assert start_schedule(date(2027, 12, 25), 3) == [
        date(2028, 1, 1),
        date(2028, 1, 1),
        date(2028, 1, 8),
    ]


def test_forecast_finish_date_by_remaining() -> None:
    latest = date(2026, 8, 22)
    assert forecast_finish_date(latest, 1) == date(2026, 8, 29)
    assert forecast_finish_date(latest, 3) == date(2026, 9, 12)
    # Брать больше нечего — даты нет.
    assert forecast_finish_date(latest, 0) is None
    assert forecast_finish_date(latest, -2) is None


def test_forecast_available_only_for_live_systems() -> None:
    assert FORECAST_METRICS == ("locations",)
    assert forecast_available("locations", "all")
    for code in FORECAST_LIVE_PLATFORMS:
        assert forecast_available("locations", code)
    # parkrun в России не работает: непосещённые площадки там не «осталось».
    assert not forecast_available("locations", "parkrun")
    # У остальных рейтингов прогноза нет вовсе.
    assert not forecast_available("volunteer_locations", "all")
    assert not forecast_available("runs", "all")


def _open_locations_fixture() -> _OpenLocations:
    return _OpenLocations(
        by_identity={
            "catalog:a": frozenset({"five_verst"}),
            "catalog:b": frozenset({"five_verst", "s95"}),
            "catalog:c": frozenset({"runpark"}),
        }
    )


def test_open_locations_unit_keys_respect_platform_filter() -> None:
    open_locations = _open_locations_fixture()
    identity = _unit_key_getters({})["locations"]
    assert open_locations.unit_keys(identity) == {"catalog:a", "catalog:b", "catalog:c"}
    assert open_locations.unit_keys(identity, "five_verst") == {"catalog:a", "catalog:b"}
    assert open_locations.unit_keys(identity, "runpark") == {"catalog:c"}


def test_remaining_units_ignores_closed_locations() -> None:
    getters = _unit_key_getters({})
    open_units = _open_locations_fixture().unit_keys(getters["locations"])
    counted = {
        "catalog:a": _LocationVisits(first_date=date(2026, 8, 1), visits=1),
        # Площадка закрыта и в знаменатель не входит — остаток она не уменьшает.
        "catalog:closed": _LocationVisits(first_date=date(2026, 8, 1), visits=1),
    }
    assert _remaining_units(counted, getters, "locations", open_units) == 2


def test_remaining_units_counts_cities() -> None:
    geo = {
        "catalog:a": _LocationGeo(city="москва|москва", region="москва"),
        "catalog:b": _LocationGeo(city="москва|москва", region="москва"),
        "catalog:c": _LocationGeo(city="тверская|тверь", region="тверская"),
    }
    getters = _unit_key_getters(geo)
    open_cities = _open_locations_fixture().unit_keys(getters["cities"])
    counted = {"catalog:a": _LocationVisits(first_date=date(2026, 8, 1), visits=1)}
    # Два города всего, один закрыт визитом в один из его парков.
    assert open_cities == {"москва|москва", "тверская|тверь"}
    assert _remaining_units(counted, getters, "cities", open_cities) == 1
