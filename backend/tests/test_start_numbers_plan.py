"""Таблица планирования «Нумератора» (нужна БД)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

pytest_plugins = ["tests.test_dashboard_api"]

from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.achievements_service import (
    START_NUMBER_PLAN_WEEKS,
    StartNumberPlanError,
    build_start_numbers_plan,
)

TODAY = date(2026, 7, 26)


def _platform(db, code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db.add(platform)
        db.flush()
    return platform


def _location_with_last_event(
    db,
    platform: Platform,
    name: str,
    last_date: date,
    last_number: int,
    country: str | None = None,
) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"plan-{uuid4().hex[:8]}",
        name=name,
        country=country,
        is_cancelled=False,
        is_paused=False,
    )
    db.add(location)
    db.flush()
    db.add(
        Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{location.external_key}:{last_number}",
            event_date=last_date,
            event_number=last_number,
        )
    )
    db.flush()
    return location


def _user_with_run(db, platform: Platform, location: Location, event_number: int) -> User:
    """Пользователь, закрывший один номер старта на этой локации."""
    user = User(telegram_id=None, display_name="Plan Test")
    db.add(user)
    db.flush()
    participant = Participant(platform_id=platform.id, external_user_id=f"plan-{uuid4().hex[:8]}")
    db.add(participant)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.test/athlete/1",
        )
    )
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:done:{event_number}",
        event_date=TODAY - timedelta(days=90),
        event_number=event_number,
    )
    db.add(event)
    db.flush()
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:1",
            position=1,
            finish_time_sec=1500,
        )
    )
    db.commit()
    return user


def _entries(plan: dict, number: int) -> list[list[str]]:
    """Названия локаций по недельным окнам для одного номера."""
    row = next(r for r in plan["rows"] if r["number"] == number)
    return [[str(entry["location"]) for entry in week] for week in row["weeks"]]


def test_plan_buckets_by_calendar_week_not_by_lag(db_session) -> None:
    """Отставшая локация попадает в то же окно, что идущая вровень.

    «Ровная» пробежала вчера (25.07) — её №101 выпадает на 01.08, окно W0.
    «Отставшая» последний раз бежала 18.07 — её №51 тоже выпадает на 01.08,
    хотя это уже «+2 недели» от её последнего старта. Раньше окно считалось по
    счётчику цикла, и такие старты разъезжались по разным колонкам.
    """
    try:
        five_verst = _platform(db_session, "five_verst")
    except Exception:
        pytest.skip("Database not available")

    on_track = _location_with_last_event(db_session, five_verst, "Ровная", date(2026, 7, 25), 100)
    lagging = _location_with_last_event(db_session, five_verst, "Отставшая", date(2026, 7, 18), 49)
    user = _user_with_run(db_session, five_verst, on_track, event_number=7)

    plan = build_start_numbers_plan(db_session, user.id, code="start_numbers", today=TODAY)

    assert _entries(plan, 101)[0] == ["Ровная"]
    assert _entries(plan, 51)[0] == ["Отставшая"]
    # И дальше обе идут по своим номерам в следующих окнах.
    assert _entries(plan, 102)[1] == ["Ровная"]
    assert _entries(plan, 52)[1] == ["Отставшая"]
    assert lagging.name == "Отставшая"


def test_plan_marks_numbers_closed_by_user(db_session) -> None:
    try:
        five_verst = _platform(db_session, "five_verst")
    except Exception:
        pytest.skip("Database not available")

    location = _location_with_last_event(db_session, five_verst, "Своя", date(2026, 7, 25), 100)
    user = _user_with_run(db_session, five_verst, location, event_number=7)

    plan = build_start_numbers_plan(db_session, user.id, code="start_numbers", today=TODAY)
    done = {row["number"] for row in plan["rows"] if row["done"]}

    assert 7 in done
    assert 8 not in done


def test_plan_covers_full_challenge_range_and_three_weeks(db_session) -> None:
    try:
        five_verst = _platform(db_session, "five_verst")
    except Exception:
        pytest.skip("Database not available")

    location = _location_with_last_event(db_session, five_verst, "Диапазон", date(2026, 7, 25), 100)
    user = _user_with_run(db_session, five_verst, location, event_number=7)

    plan = build_start_numbers_plan(db_session, user.id, code="start_numbers", today=TODAY)
    numbers = [row["number"] for row in plan["rows"]]

    assert numbers == list(range(1, 201))
    assert plan["week_count"] == START_NUMBER_PLAN_WEEKS
    assert all(len(row["weeks"]) == START_NUMBER_PLAN_WEEKS for row in plan["rows"])

    pro = build_start_numbers_plan(db_session, user.id, code="start_numbers_pro", today=TODAY)
    assert [row["number"] for row in pro["rows"]] == list(range(201, 401))


def test_plan_rejects_challenge_without_number_range(db_session) -> None:
    try:
        _platform(db_session, "five_verst")
    except Exception:
        pytest.skip("Database not available")

    user = User(telegram_id=None, display_name="No Range")
    db_session.add(user)
    db_session.commit()

    with pytest.raises(StartNumberPlanError):
        build_start_numbers_plan(db_session, user.id, code="palindrome", today=TODAY)


def test_plan_skips_paused_and_cancelled_locations(db_session) -> None:
    try:
        five_verst = _platform(db_session, "five_verst")
    except Exception:
        pytest.skip("Database not available")

    live = _location_with_last_event(db_session, five_verst, "Живая", date(2026, 7, 25), 100)
    paused = _location_with_last_event(db_session, five_verst, "НаПаузе", date(2026, 7, 25), 120)
    paused.is_paused = True
    cancelled = _location_with_last_event(db_session, five_verst, "Отменённая", date(2026, 7, 25), 140)
    cancelled.is_cancelled = True
    user = _user_with_run(db_session, five_verst, live, event_number=7)

    plan = build_start_numbers_plan(db_session, user.id, code="start_numbers", today=TODAY)

    assert _entries(plan, 101)[0] == ["Живая"]
    assert _entries(plan, 121) == [[], [], []]
    assert _entries(plan, 141) == [[], [], []]


def test_plan_scopes_to_platform_filter(db_session) -> None:
    """Фильтр систем со страницы достижений сужает и планирование.

    Иначе под фильтром «5 вёрст» таблица предлагала бы старты s95 и runpark,
    которые в этом скоупе всё равно не засчитаются, а отметка «закрыто» ставилась
    бы по номерам из чужой системы.
    """
    try:
        five_verst = _platform(db_session, "five_verst")
        s95 = _platform(db_session, "s95")
    except Exception:
        pytest.skip("Database not available")

    fv_loc = _location_with_last_event(db_session, five_verst, "Пятивёрстовая", date(2026, 7, 25), 100)
    _location_with_last_event(db_session, s95, "Эска", date(2026, 7, 25), 40)
    user = _user_with_run(db_session, five_verst, fv_loc, event_number=7)

    all_systems = build_start_numbers_plan(db_session, user.id, code="start_numbers", today=TODAY)
    assert _entries(all_systems, 101)[0] == ["Пятивёрстовая"]
    assert _entries(all_systems, 41)[0] == ["Эска"]

    only_fv = build_start_numbers_plan(
        db_session, user.id, code="start_numbers", platform_code="five_verst", today=TODAY
    )
    assert only_fv["platform_code"] == "five_verst"
    assert _entries(only_fv, 101)[0] == ["Пятивёрстовая"]
    # Старт s95 из таблицы уходит целиком.
    assert _entries(only_fv, 41) == [[], [], []]

    only_s95 = build_start_numbers_plan(
        db_session, user.id, code="start_numbers", platform_code="s95", today=TODAY
    )
    assert _entries(only_s95, 41)[0] == ["Эска"]
    assert _entries(only_s95, 101) == [[], [], []]
    # Пробежка была в 5 вёрстах — под фильтром s95 номер не считается закрытым.
    assert {row["number"] for row in only_s95["rows"] if row["done"]} == set()


def test_plan_keeps_foreign_live_platforms(db_session) -> None:
    """Зарубежные площадки действующих систем прогноз показывает.

    У 5 вёрст, s95 и RunPark есть точки в Беларуси, Сербии, Грузии — для тамошних
    участников это домашние старты, и подсказка им нужна ровно так же. Отсекается
    только мировой parkrun: от зарубежной его площадки в БД лежат лишь строки
    наших же участников из их профилей, закрыть по ней номер нельзя.
    """
    try:
        five_verst = _platform(db_session, "five_verst")
        s95 = _platform(db_session, "s95")
        runpark = _platform(db_session, "runpark")
        parkrun = _platform(db_session, "parkrun")
    except Exception:
        pytest.skip("Database not available")

    _location_with_last_event(db_session, five_verst, "БезСтраны", date(2026, 7, 25), 120, country=None)
    _location_with_last_event(db_session, s95, "Белград", date(2026, 7, 25), 140, country="Сербия")
    _location_with_last_event(db_session, runpark, "Тбилиси", date(2026, 7, 25), 180, country="Грузия")
    _location_with_last_event(db_session, parkrun, "Westpark", date(2026, 7, 25), 150, country="United Kingdom")
    user = _user_with_run(db_session, five_verst, _location_with_last_event(
        db_session, five_verst, "Домашняя", date(2026, 7, 25), 160, country="Россия"
    ), event_number=7)

    plan = build_start_numbers_plan(db_session, user.id, code="start_numbers", today=TODAY)

    assert "БезСтраны" in _entries(plan, 121)[0]
    assert "Белград" in _entries(plan, 141)[0]
    assert "Тбилиси" in _entries(plan, 181)[0]
    # Мировой parkrun по-прежнему за бортом.
    assert all("Westpark" not in week for week in _entries(plan, 151))
