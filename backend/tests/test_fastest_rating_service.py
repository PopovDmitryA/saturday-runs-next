"""Рейтинг быстрых: два зачёта одной таблицы и отсечки исходных данных."""

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
from app.services.fastest_rating_service import (
    AGE_GROUP_ALL,
    FASTEST_MODES,
    MODE_LIMITS,
    YEAR_ALL,
    _build_rank_index,
    _cut_rows,
    _format_limit,
    _merge_bests,
    _rank_from_index,
    _rank_rows,
    _slice_filters,
    get_fastest_rating,
    normalize_gender,
    normalize_mode,
    normalize_platform,
    normalize_year,
)
from app.services.leaderboard_service import _SiteLink


def test_rank_rows_ties_share_place() -> None:
    # Спортивное место: равные времена делят место, следующий получает свой
    # порядковый номер (1, 2, 2, 4) — на пятёрке ничьих в секунду много.
    rows = [{"finish_time_sec": t} for t in (900, 950, 950, 1000)]
    _rank_rows(rows)
    assert [row["rank"] for row in rows] == [1, 2, 2, 4]


def test_rank_rows_empty() -> None:
    rows: list[dict[str, object]] = []
    _rank_rows(rows)
    assert rows == []


def test_merge_bests_joins_systems_of_one_person() -> None:
    """Ядро зачёта участников: 5 вёрст и parkrun одного зарегистрированного
    человека — одна строка с его лучшим временем из обеих систем."""
    five_verst_pid = UUID(int=1)
    parkrun_pid = UUID(int=2)
    stranger_pid = UUID(int=3)
    account = _SiteLink(user_id=UUID(int=100), serial_id=7, display_name="Я", private=False)
    links = {five_verst_pid: account, parkrun_pid: account}

    merged = _merge_bests(
        [(five_verst_pid, 1100), (parkrun_pid, 1050), (stranger_pid, 1200)], links
    )

    assert len(merged) == 2
    assert merged[f"u:{account.user_id}"] == (1050, parkrun_pid)
    assert merged[f"p:{stranger_pid}"] == (1200, stranger_pid)


def test_merge_bests_ties_are_deterministic() -> None:
    # При равном времени берём меньший id участника: иначе порядок строк
    # плавал бы между пересчётами при одинаковых секундах.
    account = _SiteLink(user_id=UUID(int=100), serial_id=7, display_name="Я", private=False)
    links = {UUID(int=1): account, UUID(int=2): account}
    merged = _merge_bests([(UUID(int=2), 900), (UUID(int=1), 900)], links)
    assert merged[f"u:{account.user_id}"] == (900, UUID(int=1))


def test_rank_index_matches_plain_counting() -> None:
    """Индекс мест обязан давать ровно то же, что COUNT «сколько строго быстрее»
    плюс единица: по нему считается место в строке «Вы»."""
    counts = {900: 1, 950: 3, 1000: 2}
    index = _build_rank_index(counts)

    assert _rank_from_index(index, 900) == 1
    # Трое на 950 делят место 2 — как и в таблице.
    assert _rank_from_index(index, 950) == 2
    assert _rank_from_index(index, 1000) == 5
    # Времени в срезе нет — место всё равно считается: быстрее четверо.
    assert _rank_from_index(index, 975) == 5
    # Медленнее всех: позади весь срез.
    assert _rank_from_index(index, 3600) == 7
    # Быстрее всех.
    assert _rank_from_index(index, 800) == 1


def test_rank_index_of_empty_slice() -> None:
    assert _rank_from_index(_build_rank_index({}), 1200) == 1


def test_mode_limits_match_spec() -> None:
    # Глубина зачётов зафиксирована постановкой: 5000 финишей и 3000 участников.
    assert set(MODE_LIMITS) == set(FASTEST_MODES)
    assert MODE_LIMITS["results"] == 5000
    assert MODE_LIMITS["runners"] == 3000


def test_normalize_mode_and_gender_fall_back() -> None:
    assert normalize_mode("runners") == "runners"
    assert normalize_mode("results") == "results"
    assert normalize_mode("что-то другое") == "results"
    assert normalize_gender("female") == "female"
    assert normalize_gender("male") == "male"
    assert normalize_gender("unknown") == "all"


def test_age_group_locks_platform_to_five_verst() -> None:
    # Диапазон возраста печатают только 5 вёрст — выбор группы приводит систему
    # к ним, иначе кнопка «Система» стояла бы в невозможном положении.
    assert normalize_platform("s95", "35–39") == "five_verst"
    assert normalize_platform("all", "35–39") == "five_verst"
    # Без возрастного среза система выбирается свободно.
    assert normalize_platform("s95", AGE_GROUP_ALL) == "s95"
    assert normalize_platform("нет такой", AGE_GROUP_ALL) == "all"


def test_normalize_year_accepts_only_known_years() -> None:
    years = [2026, 2025, 2024]
    assert normalize_year("2025", years) == "2025"
    assert normalize_year("2013", years) == YEAR_ALL
    assert normalize_year("не год", years) == YEAR_ALL
    assert normalize_year(YEAR_ALL, years) == YEAR_ALL


def test_cut_rows_does_not_touch_the_snapshot() -> None:
    payload = {"rows": [1, 2, 3], "entrants": 3}
    short = _cut_rows(payload, 2)
    assert short["rows"] == [1, 2]
    # Знаменатель остаётся от полного среза: limit укорачивает вид, а не зачёт.
    assert short["entrants"] == 3
    assert payload["rows"] == [1, 2, 3]
    assert _cut_rows(payload, None) is payload
    assert _cut_rows(payload, 10) is payload


def test_format_limit_uses_non_breaking_space() -> None:
    assert _format_limit(5000) == "5 000"


def test_slice_filters_build_expected_clauses(db_session: Session) -> None:
    where, params = _slice_filters(
        db_session,
        platform="five_verst",
        gender="female",
        age_group=AGE_GROUP_ALL,
        year="2025",
        options={"age_categories": {}},
    )
    assert "p.code = :platform" in where
    assert "pa.gender = :gender" in where
    assert params["year_start"] == date(2025, 1, 1)
    assert params["year_end"] == date(2025, 12, 31)
    assert "rr.age_category" not in where

    where_age, params_age = _slice_filters(
        db_session,
        platform="five_verst",
        gender="all",
        age_group="35–39",
        year=YEAR_ALL,
        options={"age_categories": {"35–39": ["М35-39", "Ж35-39"]}},
    )
    assert "rr.age_category = ANY(:age_categories)" in where_age
    assert params_age["age_categories"] == ["М35-39", "Ж35-39"]


def _seed_five_verst_runner(
    db_session: Session,
    *,
    times: list[int],
    gender: str | None = "male",
    age_category: str | None = "М35-39",
) -> UUID:
    """Один бегун 5 вёрст с перечисленными результатами на разных стартах."""
    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"fastest-{suffix}",
        display_name=f"Быстрый Тестовый {suffix}",
        gender=gender,
    )
    db_session.add(participant)
    db_session.flush()

    location = Location(
        platform_id=platform.id,
        external_key=f"fastest-loc-{suffix}",
        name="Тестовый парк",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    for index, seconds in enumerate(times):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"fastest-event-{suffix}-{index}",
            event_date=date(2025, 6, 7 + index * 7),
            event_number=index + 1,
            finishers_count=1,
            runners_count=1,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"fastest-result-{suffix}-{index}",
                position=1,
                finish_time_sec=seconds,
                finish_time_display="00:10:00",
                age_category=age_category,
                status="finished",
            )
        )
    db_session.flush()
    return participant.id


def test_response_limit_does_not_shrink_the_standing(db_session: Session) -> None:
    """limit укорачивает ответ, но не сам зачёт: глубина в payload остаётся
    полной. Ловит подмену глубины зачёта параметром ответа — карточка на хабе
    просит три строки, и рейтинг не должен думать, что он трёхстрочный."""
    payload = get_fastest_rating(db_session, mode="results", limit=3, use_cache=False)
    assert len(payload["rows"]) <= 3
    assert payload["limit"] == MODE_LIMITS["results"]


def _rows_of(db_session: Session, mode: str, **filters: str) -> list[dict[str, object]]:
    payload = get_fastest_rating(db_session, mode=mode, use_cache=False, **filters)
    return payload["rows"]


def test_results_keep_repeats_and_runners_keep_one_row(db_session: Session) -> None:
    """Ровно то, чем два зачёта различаются: в результатах человек занимает
    столько строк, сколько у него быстрых забегов, в участниках — одну."""
    # Времена заведомо быстрее любых реальных: строки гарантированно наверху.
    participant_id = _seed_five_verst_runner(db_session, times=[500, 520, 540])

    results = _rows_of(db_session, "results")
    mine = [row for row in results if row["finish_time_sec"] in (500, 520, 540)]
    assert [row["finish_time_sec"] for row in mine] == [500, 520, 540]
    assert [row["rank"] for row in mine] == [1, 2, 3]

    runners = _rows_of(db_session, "runners")
    mine_runners = [row for row in runners if row["finish_time_sec"] in (500, 520, 540)]
    assert len(mine_runners) == 1
    assert mine_runners[0]["finish_time_sec"] == 500
    assert mine_runners[0]["rank"] == 1
    assert participant_id is not None


def test_gender_slice_drops_runners_without_gender(db_session: Session) -> None:
    """Строка, у которой система не назвала пол, в гендерный срез не идёт —
    и при этом ничьё время не искажает: мест среди своего пола тут нет."""
    _seed_five_verst_runner(db_session, times=[505], gender=None)

    absolute = _rows_of(db_session, "results")
    assert any(row["finish_time_sec"] == 505 for row in absolute)

    for gender in ("male", "female"):
        gendered = _rows_of(db_session, "results", gender=gender)
        assert all(row["finish_time_sec"] != 505 for row in gendered)


def _seed_parkrun_runner(db_session: Session, *, russian_seconds: int, foreign_seconds: int) -> None:
    """parkrun-бегун с результатом на русской и на зарубежной площадке."""
    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        platform = Platform(code="parkrun", name="parkrun")
        db_session.add(platform)
        db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"fastest-parkrun-{suffix}",
        display_name=f"Parkrun Tester {suffix}",
        gender="male",
    )
    db_session.add(participant)
    db_session.flush()

    for index, (seconds, is_russian) in enumerate(
        ((russian_seconds, True), (foreign_seconds, False))
    ):
        location = Location(
            platform_id=platform.id,
            external_key=f"fastest-parkrun-loc-{suffix}-{index}",
            name="Parkrun Test Park",
            country="Россия" if is_russian else "United Kingdom",
        )
        db_session.add(location)
        db_session.flush()
        if is_russian:
            catalog = LocationCatalog(
                canonical_name=f"Parkrun Test Park {suffix}-{index}",
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
            external_event_key=f"fastest-parkrun-event-{suffix}-{index}",
            event_date=date(2019, 6, 1 + index),
            event_number=100 + index,
            finishers_count=1,
            runners_count=1,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"fastest-parkrun-result-{suffix}-{index}",
                position=1,
                finish_time_sec=seconds,
                finish_time_display="00:10:00",
                status="finished",
            )
        )
    db_session.flush()


def test_year_options_are_split_by_system(db_session: Session) -> None:
    """Годы предлагаются в разрезе систем: русский parkrun жил 2014–2022, а
    остальные системы стартовали в 2022-м. Общий список лет предлагал бы
    «parkrun + 2026» — заведомо пустую таблицу."""
    _seed_five_verst_runner(db_session, times=[1500])  # старт 2025 года
    _seed_parkrun_runner(db_session, russian_seconds=1500, foreign_seconds=1400)  # 2019

    payload = get_fastest_rating(db_session, mode="results", use_cache=False)
    years = payload["year_options_by_platform"]

    assert 2025 in years["five_verst"]
    assert 2019 in years["parkrun"]
    assert 2025 not in years["parkrun"]
    assert 2019 not in years["five_verst"]
    # Объединение под ключом "all" содержит и то и другое.
    assert {2019, 2025} <= set(years["all"])


def test_year_absent_for_the_system_falls_back_to_all_time(db_session: Session) -> None:
    """Год, которого у выбранной системы нет, молча превращается в «за всё
    время», а не в пустую таблицу."""
    _seed_parkrun_runner(db_session, russian_seconds=1500, foreign_seconds=1400)
    payload = get_fastest_rating(
        db_session, mode="results", platform="parkrun", year="2026", use_cache=False
    )
    assert payload["year"] == YEAR_ALL


def test_foreign_parkrun_result_stays_out(db_session: Session) -> None:
    """Зарубежный parkrun в рейтинг не идёт даже более быстрым результатом: от
    такой площадки в базе только строка из профиля самого участника."""
    _seed_parkrun_runner(db_session, russian_seconds=560, foreign_seconds=480)

    rows = _rows_of(db_session, "results")
    assert any(row["finish_time_sec"] == 560 for row in rows)
    assert all(row["finish_time_sec"] != 480 for row in rows)
