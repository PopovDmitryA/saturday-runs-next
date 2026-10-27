"""Рейтинги челленджей: места, варианты счёта, срезы по системам, витрина."""

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import ChallengeRatingSnapshot, User
from app.services import challenge_rating_service
from app.services.achievements_service import CHALLENGE_TIERS
from app.services.challenge_rating_service import (
    _RUN_ROWS_SQL,
    _VOLUNTEER_ROWS_SQL,
    ALL,
    CHALLENGES_BY_CODE,
    MISSING_LIMIT,
    RATING_CHALLENGES,
    _GridBuilder,
    _is_placeholder,
    _missing,
    _Run,
    _snapshot,
    _to_next_plan,
    _Volunteering,
    board_keys,
    entity_metrics,
    get_challenge_hub,
    get_challenge_rating,
    get_my_challenge_row,
    normalize_platform,
    platform_filter_values,
    variant_of,
)
from app.services.leaderboard_service import _SiteLink

WEEK_START = date(2026, 10, 19)
SAT = date(2026, 9, 5)
ALPHABET = {ALL: frozenset("АБВ"), "five_verst": frozenset("АБ"), "parkrun": frozenset("ВГ")}


def _runs(platform: str, locations: list[str], start: date = SAT) -> list[_Run]:
    return [
        _Run(
            platform=platform,
            event_date=start + timedelta(days=7 * index),
            location=location,
            finish_time_sec=1500 + index,
            position=10 + index,
            event_number=index + 1,
        )
        for index, location in enumerate(locations)
    ]


def _grid(**kwargs: object) -> _GridBuilder:
    return _GridBuilder(WEEK_START, alphabet=ALPHABET, **kwargs)  # type: ignore[arg-type]


def _board_rows(
    grid: _GridBuilder, code: str, scope: str = ALL, variant: str = "main", activity: str = "all"
) -> list[dict[str, object]]:
    key = (code, variant, scope, activity)
    return _snapshot(key, grid.boards[key], {}, WEEK_START, WEEK_START, "t")["rows"]


def test_registry_is_consistent() -> None:
    assert len({item.slug for item in RATING_CHALLENGES}) == len(RATING_CHALLENGES)
    for item in RATING_CHALLENGES:
        assert item.code in CHALLENGE_TIERS, item.code
        assert len({variant.key for variant in item.variants}) == len(item.variants)
    # V-индекс без parkrun — как и карточка: у его волонтёрств нет ни даты, ни локации.
    assert "parkrun" not in platform_filter_values(CHALLENGES_BY_CODE["v_index"])
    assert "parkrun" in platform_filter_values(CHALLENGES_BY_CODE["p_index"])
    streak = CHALLENGES_BY_CODE["streak"]
    assert "parkrun" not in platform_filter_values(streak, variant_of(streak, "volunteering"))
    assert "parkrun" in platform_filter_values(streak, variant_of(streak, "runs"))
    # 10 челленджей; серия — 4 варианта, «Нумератор», Уилсон и «Ударный год» —
    # по 2; без parkrun у V-индекса и волонтёрской серии; всё × «Все/Активные».
    assert len(board_keys()) == 156


def test_normalize_platform_and_variant_fall_back() -> None:
    v_index = CHALLENGES_BY_CODE["v_index"]
    assert normalize_platform(v_index, "s95") == "s95"
    assert normalize_platform(v_index, "parkrun") == ALL
    assert normalize_platform(v_index, "нет такой") == ALL
    numbers = CHALLENGES_BY_CODE["start_numbers"]
    assert variant_of(numbers, "pro").key == "pro"
    assert variant_of(numbers, None).key == "base"
    assert variant_of(numbers, "нет такого").key == "base"


def test_placeholders_are_not_people() -> None:
    """Решение Дмитрия 27.10.2026: «Неизвестный» и безымянные — не в зачёт."""
    assert _is_placeholder("anon:3f2a", "Неизвестный бегун")
    assert _is_placeholder("unknown:kuzminki:2026-10-24:15", "Иван Петров")
    assert _is_placeholder("123", "Неизвестный")
    assert _is_placeholder("123", "")
    assert not _is_placeholder("123", "Иван Петров")


def test_equal_value_and_date_share_a_place() -> None:
    grid = _grid()
    early, late, twin = UUID(int=1), UUID(int=2), UUID(int=3)
    # «60 секунд» 4 у всех троих: четыре финиша с разными секундами.
    grid.add(f"p:{late}", (late,), _runs("five_verst", ["a"] * 4, SAT + timedelta(7)), [], None)
    grid.add(f"p:{early}", (early,), _runs("five_verst", ["a"] * 4), [], None)
    grid.add(f"p:{twin}", (twin,), _runs("five_verst", ["b"] * 4), [], None)

    rows = _board_rows(grid, "seconds")
    assert [row["value"] for row in rows] == [4, 4, 4]
    # У «early» и «twin» лестницы совпадают целиком — место общее.
    assert [row["rank"] for row in rows] == [1, 1, 3]
    assert rows[2]["reached_on"] == (SAT + timedelta(28)).isoformat()


def test_earlier_previous_step_breaks_the_tie() -> None:
    """Р34: значение и день набора одни — выше тот, кто раньше дошёл до
    предыдущей ступени."""
    grid = _grid()
    fast, slow = UUID(int=1), UUID(int=2)
    last = SAT + timedelta(28)
    fast_runs = _runs("s95", ["a"] * 2, SAT) + [_Run("s95", last, "a", 1502, 1, 3)]
    slow_runs = _runs("s95", ["a"] * 2, SAT + timedelta(14)) + [_Run("s95", last, "a", 1502, 1, 3)]
    grid.add(f"p:{slow}", (slow,), slow_runs, [], None)
    grid.add(f"p:{fast}", (fast,), fast_runs, [], None)
    rows = _board_rows(grid, "seconds")
    assert [row["value"] for row in rows] == [3, 3]
    assert [row["reached_on"] for row in rows] == [last.isoformat(), last.isoformat()]
    assert [row["rank"] for row in rows] == [1, 2]


def test_hirsch_tie_is_broken_by_what_is_left_to_the_next_index() -> None:
    """p-индекс: при равном индексе выше тот, кому меньше до следующего, —
    даже если набрал индекс позже. Дата при этом остаётся датой набора."""
    grid = _grid()
    early, close = UUID(int=1), UUID(int=2)
    grid.add(f"p:{early}", (early,), _runs("five_verst", ["a", "b", "a", "b"]), [], None)
    grid.add(
        f"p:{close}",
        (close,),
        _runs("five_verst", ["a", "b", "c", "a", "b", "a", "b"], SAT + timedelta(7)),
        [],
        None,
    )
    rows = _board_rows(grid, "p_index")
    assert [(row["value"], row["to_next"]) for row in rows] == [(2, 2), (2, 5)]
    assert [row["rank"] for row in rows] == [1, 2]
    assert rows[0]["reached_on"] == (SAT + timedelta(7 + 28)).isoformat()


def test_site_user_wins_a_full_tie_at_the_top_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аудит 27.10.2026: при полной ничьей на границе топа в таблицу проходит
    зарегистрированный, иначе ему с тем же местом писалось «вы пока ниже»."""
    monkeypatch.setattr(challenge_rating_service, "TOP_LIMIT", 2)
    grid = _grid()
    for index in range(3):
        pid = UUID(int=index + 1)
        grid.add(f"p:{pid}", (pid,), _runs("s95", ["a"]), [], None)
    account = _SiteLink(user_id=UUID(int=100), serial_id=7, display_name="Я", private=False)
    grid.add(f"u:{account.user_id}", (UUID(int=50),), _runs("s95", ["a"]), [], account)
    key = ("seconds", "main", ALL, "all")
    snapshot = _snapshot(key, grid.boards[key], {}, WEEK_START, WEEK_START, "t")
    assert len(snapshot["rows"]) == 2
    assert snapshot["site_rows"][str(account.user_id)]["included"] is True


def test_week_delta_and_new_entrant() -> None:
    grid = _grid()
    veteran, newcomer = UUID(int=1), UUID(int=2)
    # Ветеран: «Нумератор» 3 к началу недели, +1 на этой неделе.
    runs = _runs("s95", ["a", "a", "a"], date(2026, 9, 26))
    runs.append(_Run("s95", date(2026, 10, 24), "a", 1600, 50, 4))
    grid.add(f"p:{veteran}", (veteran,), runs, [], None)
    grid.add(f"p:{newcomer}", (newcomer,), [_Run("s95", date(2026, 10, 24), "a", 1600, 51, 9)], [], None)

    rows = sorted(_board_rows(grid, "start_numbers", variant="base"), key=lambda row: row["rank"])
    assert rows[0]["value"] == 4
    assert rows[0]["value_delta"] == 1
    assert rows[0]["rank_delta"] == 0
    # Неделю назад значения не было — стрелки нет.
    assert rows[1]["rank_delta"] is None
    assert rows[1]["value_delta"] == 1


def test_wilson_variants_show_where_the_chain_runs() -> None:
    grid = _grid()
    pid = UUID(int=1)
    numbers = [1, 2, 3, 20, 21, 22, 23, 24]
    runs = [_Run("five_verst", SAT + timedelta(7 * i), "a", 1500, 1, n) for i, n in enumerate(numbers)]
    grid.add(f"p:{pid}", (pid,), runs, [], None)
    classic = _board_rows(grid, "wilson", variant="classic")[0]
    assert (classic["value"], classic["chain_from"], classic["chain_to"]) == (3, 1, 3)
    floating = _board_rows(grid, "wilson", variant="floating")[0]
    assert (floating["value"], floating["chain_from"], floating["chain_to"]) == (5, 20, 24)
    assert floating["reached_on"] == (SAT + timedelta(49)).isoformat()


def test_numbers_pro_counts_201_to_400() -> None:
    grid = _grid()
    pid = UUID(int=1)
    grid.add(f"p:{pid}", (pid,), [_Run("five_verst", SAT, "a", 1500, 1, 205), _Run("s95", SAT, "b", 1500, 1, 7)], [], None)
    assert _board_rows(grid, "start_numbers", variant="pro")[0]["value"] == 1
    assert _board_rows(grid, "start_numbers", variant="base")[0]["value"] == 1


def test_systems_are_separate_slices_and_columns_of_the_overall_one() -> None:
    account = _SiteLink(user_id=UUID(int=100), serial_id=7, display_name="Я", private=False)
    runs = _runs("five_verst", ["a", "b"] * 3) + _runs("parkrun", ["x", "y"] * 3, date(2019, 1, 5))
    runs.sort(key=lambda run: run.event_date)
    grid = _grid()
    grid.add(f"u:{account.user_id}", (UUID(int=1), UUID(int=2)), runs, [], account)

    overall = _board_rows(grid, "p_index")[0]
    # В каждой системе по две локации с тремя финишами — p-индекс 2, а вместе
    # четыре такие локации дают 3: общий зачёт — не максимум из систем.
    assert overall["value"] == 3
    assert overall["platforms"] == {"five_verst": 2, "parkrun": 2}
    assert overall["site_serial_id"] == 7
    assert overall["display_name"] == "Я"
    five_verst = _board_rows(grid, "p_index", "five_verst")[0]
    assert five_verst["value"] == 2
    assert "platforms" not in five_verst
    board = grid.boards[("p_index", "main", ALL, "all")]
    assert [candidate.user_id for candidate in board.site] == [account.user_id]


def test_active_slice_drops_those_who_stopped_running() -> None:
    """Р35: «Активные» — выходившие на старт за последний год."""
    grid = _grid()
    old, fresh = UUID(int=1), UUID(int=2)
    grid.add(f"p:{old}", (old,), _runs("parkrun", ["x"] * 5, date(2019, 1, 5)), [], None)
    grid.add(f"p:{fresh}", (fresh,), _runs("s95", ["a"]), [], None)
    everyone = _board_rows(grid, "seconds")
    assert {row["value"] for row in everyone} == {5, 1}
    assert everyone[0]["last_seen"] == date(2019, 2, 2).isoformat()
    active = _board_rows(grid, "seconds", activity="active")
    assert [row["value"] for row in active] == [1]


def test_single_system_participant_reuses_the_overall_slice() -> None:
    grid = _grid()
    scopes = entity_metrics(_runs("runpark", ["a", "a"]), [], grid.context)
    assert scopes[ALL].metrics[("p_index", "main")] is scopes["runpark"].metrics[("p_index", "main")]


def test_streak_variants_and_v_index_roles() -> None:
    vols = [
        _Volunteering("five_verst", SAT, "a", True),
        _Volunteering("five_verst", SAT + timedelta(7), "b", True),
        _Volunteering("five_verst", SAT + timedelta(14), "a", True),
        _Volunteering("five_verst", SAT + timedelta(21), "b", False),
    ]
    # Пробежки — через неделю после последнего волонтёрства, две недели подряд.
    runs = _runs("five_verst", ["a", "a"], SAT + timedelta(28))
    grid = _GridBuilder(WEEK_START, anchor=SAT + timedelta(35), alphabet=ALPHABET)
    metrics = entity_metrics(runs, vols, grid.context)[ALL].metrics
    # Роль последнего выхода не узнана — V-индекс 1, хотя визитов на «b» два.
    assert len(metrics[("v_index", "main")].growth) == 1
    # Серия: всё вместе — 6 недель, только волонтёрство — 4, только пробежки — 2.
    assert len(metrics[("streak", "any")].growth) == 6
    assert len(metrics[("streak", "volunteering")].growth) == 4
    assert len(metrics[("streak", "runs")].growth) == 2
    # Р31: общая серия идёт (последняя неделя — неделя последнего старта базы),
    # волонтёрская оборвалась две недели назад.
    assert metrics[("streak", "any")].ongoing is True
    assert metrics[("streak", "volunteering")].ongoing is False
    assert len(metrics[("streak", "current")].growth) == 6


def test_best_year_this_year_variant_and_column() -> None:
    """Р32: «Этот год» — отдельный рейтинг, а число этого года видно и в
    рейтинге лучшего года."""
    grid = _grid()
    pid = UUID(int=1)
    runs = _runs("s95", ["a"] * 5, date(2025, 3, 1)) + _runs("s95", ["a"] * 2, date(2026, 3, 7))
    grid.add(f"p:{pid}", (pid,), runs, [], None)
    best = _board_rows(grid, "best_year", variant="best")[0]
    assert (best["value"], best["this_year"]) == (5, 2)
    this_year = _board_rows(grid, "best_year", variant="this_year")[0]
    assert this_year["value"] == 2


def test_alphabet_follows_the_card_rule() -> None:
    """В общем зачёте parkrun не считается; буква засчитывается, только если
    она есть в каталоге среза; недостающие — по алфавиту."""
    grid = _grid()
    pid = UUID(int=1)
    runs = [
        _Run("five_verst", SAT, "a", 1500, 1, 1, letter="А"),
        _Run("parkrun", SAT + timedelta(7), "x", 1500, 1, 1, letter="В"),
        _Run("five_verst", SAT + timedelta(14), "c", 1500, 1, 2, letter="Я"),
    ]
    grid.add(f"p:{pid}", (pid,), runs, [], None)
    overall = _board_rows(grid, "alphabet")[0]
    assert overall["value"] == 1
    assert overall["missing"] == ["Б", "В"]
    assert _board_rows(grid, "alphabet", "parkrun")[0]["value"] == 1


def test_missing_seconds_and_nearest_calendar_days() -> None:
    seconds, total = _missing("seconds", set(range(60)) - {7, 42}, SAT)
    assert (seconds, total) == ([7, 42], 2)
    days, total = _missing("calendar_days", {(12, 31)}, date(2026, 12, 30))
    assert total == 365
    assert days[0] == "01-01"
    assert len(days) == MISSING_LIMIT
    days, _total = _missing("calendar_days", set(), date(2026, 10, 24))
    assert days[:2] == ["10-25", "10-26"]


def test_to_next_plan_takes_the_cheapest_path() -> None:
    """Р33: где добрать до индекса +1 — с самых обжитых локаций."""
    names = {"a": "Измайлово", "b": "Терлецкий", "c": "Кузьминки"}
    plan = _to_next_plan({"a": 3, "b": 2, "c": 1}, 2, names)
    # p=2 → нужно три локации по 3: Измайлово уже, Терлецкий +1, Кузьминки +2.
    assert plan == [{"location": "Терлецкий", "add": 1}, {"location": "Кузьминки", "add": 2}]
    # Локаций не хватает — новые полными порциями.
    assert _to_next_plan({"a": 1}, 1, names) == [
        {"location": "Измайлово", "add": 1},
        {"location": None, "add": 2, "count": 1},
    ]


def test_rows_carry_extra_fields_only_where_they_belong() -> None:
    grid = _grid()
    pid = UUID(int=1)
    grid.add(f"p:{pid}", (pid,), _runs("s95", ["a", "b"]), [], None)
    seconds = _board_rows(grid, "seconds")[0]
    assert seconds["missing_total"] == 58
    assert "to_next" not in seconds
    p_index = _board_rows(grid, "p_index")[0]
    assert p_index["to_next"] == 2
    assert "missing" not in p_index
    # Медалей в строке нет, а «где добрать» — только у строк «Вы».
    assert "level" not in p_index
    assert "to_next_plan" not in p_index


def test_private_profile_hides_the_link_and_site_name() -> None:
    account = _SiteLink(user_id=UUID(int=100), serial_id=7, display_name="Скрытый", private=True)
    grid = _grid()
    grid.add(f"u:{account.user_id}", (UUID(int=1),), _runs("s95", ["a"]), [], account)
    key = ("p_index", "main", ALL, "all")
    snapshot = _snapshot(key, grid.boards[key], {UUID(int=1): "Имя Из Протокола"}, WEEK_START, WEEK_START, "t")
    row = snapshot["rows"][0]
    assert row["site_serial_id"] is None
    assert row["display_name"] == "Имя Из Протокола"
    site = snapshot["site_rows"][str(account.user_id)]
    assert site["included"] is True
    assert site["to_next_plan"] == [{"location": "a", "add": 1}, {"location": None, "add": 2, "count": 1}]


def test_raw_queries_parse(db_session: Session) -> None:
    # EXPLAIN, а не выполнение: полный проход по протоколам тесту не нужен,
    # важно, что шаблоны (с CTE допуска parkrun из leaderboard_service) собраны.
    db_session.execute(text("EXPLAIN " + _RUN_ROWS_SQL))
    db_session.execute(text("EXPLAIN " + _VOLUNTEER_ROWS_SQL))


def _store(db_session: Session, user: User) -> None:
    row = {
        "rank": 1,
        "rank_delta": None,
        "row_key": "abc",
        "display_name": "Тест",
        "site_serial_id": user.serial_id,
        "value": 5,
        "value_delta": 1,
        "reached_on": "2026-10-24",
        "to_next": 3,
        "platforms": {"s95": 5},
    }

    def snapshot(code: str, variant: str, rows: list[dict[str, object]], site: dict[str, object]) -> None:
        db_session.add(
            ChallengeRatingSnapshot(
                code=code,
                variant=variant,
                platform=ALL,
                activity="all",
                rows=rows,
                site_rows=site,
                entrants=len(rows),
                latest_event_date=date(2026, 10, 24),
                week_start=date(2026, 10, 18),
                built_at=datetime.now(timezone.utc),
            )
        )

    db_session.query(ChallengeRatingSnapshot).delete()
    snapshot("p_index", "main", [row, {**row, "rank": 2, "row_key": "def"}], {str(user.id): {**row, "included": True}})
    snapshot("start_numbers", "pro", [{**row, "value": 9}], {})
    db_session.flush()


@pytest.fixture
def snapshot_user(db_session: Session) -> User:
    # Свой пользователь, а не первый попавшийся: в CI база пустая, и тест
    # пропускался бы всегда (аудит 27.10.2026).
    user = User(display_name="Рейтинги Челленджей", consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    _store(db_session, user)
    return user


def test_rating_and_my_row_read_the_snapshot(db_session: Session, snapshot_user: User) -> None:
    p_index = CHALLENGES_BY_CODE["p_index"]
    payload = get_challenge_rating(db_session, p_index, "all", limit=1)
    assert payload["ready"] is True
    assert len(payload["rows"]) == 1
    assert payload["entrants"] == 2
    assert payload["variants"] == []
    assert payload["to_next_forms"] == ["финиш", "финиша", "финишей"]

    mine = get_my_challenge_row(db_session, p_index, snapshot_user)
    assert mine["included"] is True
    assert mine["row"]["to_next"] == 3

    # Снимка среза нет — пустая таблица с признаком «ещё считается», не 500.
    missing = get_challenge_rating(db_session, p_index, "s95")
    assert missing["ready"] is False
    assert missing["rows"] == []
    assert get_challenge_rating(db_session, p_index, "all", activity="active")["ready"] is False


def test_variant_is_read_from_its_own_snapshot(db_session: Session, snapshot_user: User) -> None:
    numbers = CHALLENGES_BY_CODE["start_numbers"]
    pro = get_challenge_rating(db_session, numbers, "all", variant="pro")
    assert pro["variant"] == "pro"
    assert pro["rows"][0]["value"] == 9
    assert [option["key"] for option in pro["variants"]] == ["base", "pro"]
    base = get_challenge_rating(db_session, numbers, "all")
    assert base["variant"] == "base"
    assert base["ready"] is False


def test_parkrun_slice_is_marked_as_archive() -> None:
    meta = challenge_rating_service._meta(CHALLENGES_BY_CODE["p_index"], variant_of(CHALLENGES_BY_CODE["p_index"], None), "parkrun", "all")
    assert meta["scope_note"]
    assert meta["show_deltas"] is False


def test_hub_gives_top_three_and_falls_back_for_absent_system(db_session: Session, snapshot_user: User) -> None:
    hub = get_challenge_hub(db_session, snapshot_user, "parkrun")
    cards = {card["code"]: card for card in hub["cards"]}
    assert len(cards) == len(RATING_CHALLENGES)
    assert cards["v_index"]["platform"] == ALL
    assert cards["p_index"]["platform"] == "parkrun"
    assert cards["p_index"]["rows"] == []
    overall = get_challenge_hub(db_session, snapshot_user, "all")
    p_card = next(card for card in overall["cards"] if card["code"] == "p_index")
    assert [row["rank"] for row in p_card["rows"]] == [1, 2]
    assert p_card["me"]["value"] == 5
    # Карточка «Нумератора» — первый вариант (№1–200), а не ПРО.
    numbers_card = next(card for card in overall["cards"] if card["code"] == "start_numbers")
    assert numbers_card["rows"] == []


def _frontend_src() -> Path:
    for candidate in (Path("/frontend-src"), Path(__file__).resolve().parents[2] / "frontend" / "src"):
        if (candidate / "App.tsx").exists():
            return candidate
    pytest.skip("нет доступа к frontend/src")


def test_menu_and_routes_cover_every_challenge_rating() -> None:
    """Каждый рейтинг челленджа — пункт меню (группа «Челленджи» в siteNav.ts)
    и маршрут /ratings/{slug} в App.tsx: решение Дмитрия 26.10.2026 — как
    остальные рейтинги, отдельным пунктом."""
    src = _frontend_src()
    nav = (src / "features" / "portal" / "nav" / "siteNav.ts").read_text(encoding="utf-8")
    group_start = nav.index('key: "challenges"')
    group = nav[group_start : nav.index("\n  },", group_start)]
    nav_keys = re.findall(r'\{ key: "([a-z0-9-]+)"', group)
    assert nav_keys == [item.slug for item in RATING_CHALLENGES]
    app = (src / "App.tsx").read_text(encoding="utf-8")
    for item in RATING_CHALLENGES:
        assert f'"/ratings/{item.slug}": () => <ChallengeRatingPage slug="{item.slug}" />' in app, item.slug
