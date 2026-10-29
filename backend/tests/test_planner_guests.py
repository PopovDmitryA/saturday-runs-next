"""Цифры о планирующих: блок «Планируют пробежать» (рядом с именем — всего
пробежек), метки оргкалендаря («впервые у нас», «будет N-я») и раздел
оргкабинета «Кто у нас бежит» — таблица гостей со столбцом «Знаковый старт» и
страница участника (решения Дмитрия 25.10.2026). Юбилеи — только на три
ближайших старта локации."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import (
    DashboardCache,
    Event,
    EventCrosslink,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationOrganizerAccess,
    Participant,
    RunPlan,
    StartSpecialDate,
    User,
)
from app.services import planner_guests_service, run_plan_service
from app.services.achievements_service import (
    NUMBER_CHALLENGES,
    RunRow,
    VisitHistory,
    _build_challenge_list,
    _collect_run_rows,
    number_gain_rows,
    number_gains,
    visit_histories,
)
from app.services.dashboard_service import (
    ANALYTICS_VERSION,
    cabinet_headline_stats,
    compute_dashboard_stats,
    total_runs_for_users,
)
from app.services.organizer_access_service import invalidate_organizer_locations_cache
from app.services.organizer_service import is_milestone
from app.services.run_plan_service import location_plans_block
from app.services.start_calendar_service import day_label
from tests.test_plan_series import _series
from tests.test_run_plans import (
    ADMIN_TELEGRAM_ID,
    HOLIDAY,
    PAST_SAT,
    RECENT_START,
    SAT1,
    SAT2,
    TODAY,
    _event,
    _key,
    _location,
    _login,
    _participant,
    _plan,
    _public_user,
    _run,
    _user,
    _volunteer,
)

# Третья и четвёртая субботы от TODAY: три ближайших старта — SAT1, SAT2, SAT3.
SAT3 = date(2026, 11, 7)
SAT4 = date(2026, 11, 14)
HORIZON_NOTE = "Юбилеи считаем на три ближайших старта"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "app.services.start_calendar_service.moscow_today",
        "app.services.run_plan_service.moscow_today",
        "app.services.planner_guests_service.moscow_today",
        "app.api.routes.planner.moscow_today",
        "app.api.routes.organizer.moscow_today",
    ):
        monkeypatch.setattr(target, lambda: TODAY)


@pytest.fixture
def client(db_session: Session, fake_redis: fakeredis.FakeRedis) -> Generator[TestClient, None, None]:
    settings = Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        admin_telegram_id=ADMIN_TELEGRAM_ID,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _place(db: Session, name: str, *, city: str = "Москва", region: str | None = None) -> Location:
    """Локация без своих стартов: календарь ей задают события теста."""
    location = _location(db, name=name, started=False)
    location.city = city
    location.region = region
    db.flush()
    return location


def _history(db: Session, location: Location, participant: Participant, count: int, *, start: int = 1) -> list[Event]:
    """count пробежек на локации по субботам до PAST_SAT, номера start…"""
    events = []
    for index in range(count):
        on_date = PAST_SAT - timedelta(weeks=count - 1 - index)
        event = _event(db, location, on_date, start + index)
        _run(db, event, participant, finish=1500 + index)
        events.append(event)
    return events


def _cache_runs(db: Session, user: User, total: int, *, volunteering: int = 0, locations: int = 1) -> None:
    """Годный кэш кабинета: ровно эти числа показывает профиль."""
    db.add(
        DashboardCache(
            user_id=user.id,
            computed_at=datetime.now(UTC),
            stats={
                "total_runs": total,
                "total_volunteering": volunteering,
                "analytics": {"analytics_version": ANALYTICS_VERSION, "unique_locations": locations},
            },
        )
    )
    db.flush()


def _people(block: dict[str, object], on_date: date) -> dict[str, dict[str, object]]:
    day = next(item for item in block["dates"] if item["date"] == on_date)  # type: ignore[union-attr,index]
    return {person["name"]: person for person in day["people"]}


def _calendar_people(db: Session, location: Location, on_date: date) -> dict[str, dict[str, object]]:
    """Люди будущего дня оргкалендаря — с метками, которых нет на странице локации."""
    month = run_plan_service.organizer_calendar(db, _identity(location), on_date)
    return _people({"dates": month["days"]}, on_date)


def _marks(stats: planner_guests_service.PersonStats) -> list[dict[str, str]]:
    return [mark.payload() for mark in stats.marks]


def _grant(client: TestClient, db: Session, location: Location) -> User:
    viewer = _login(client, db)
    db.add(LocationOrganizerAccess(user_id=viewer.id, location_key=_key(location)))
    db.commit()
    invalidate_organizer_locations_cache(viewer.id)
    return viewer


# ---------------------------------------------------------------- правила


def test_milestone_rule_is_the_organizer_calendar_rule() -> None:
    assert [number for number in range(1, 130) if is_milestone(number)] == [10, 25, 50, 75, 100, 125]
    assert not is_milestone(0)


def test_total_runs_match_the_cabinet_and_skip_crosslink_duplicates(db_session: Session) -> None:
    """Число без кэша — тем же правилом, что плитка кабинета: дубль зеркала
    (RunPark повторяет протокол S95) не считается, тестовый старт тоже."""
    runner = _public_user(db_session, "Зеркальный")
    s95 = _participant(db_session, runner, "s95")
    runpark = _participant(db_session, runner, "runpark")
    s95_park = _location(db_session, "s95", started=False)
    mirror_park = _location(db_session, "runpark", started=False)
    primary = _event(db_session, s95_park, PAST_SAT, 30)
    secondary = _event(db_session, mirror_park, PAST_SAT, 12)
    db_session.add(EventCrosslink(primary_event_id=primary.id, secondary_event_id=secondary.id))
    _run(db_session, primary, s95)
    _run(db_session, secondary, runpark)
    _run(db_session, _event(db_session, s95_park, PAST_SAT - timedelta(weeks=1), 29), s95)
    test_event = _event(db_session, s95_park, PAST_SAT - timedelta(weeks=2), 0)
    test_event.is_test_event = True
    _run(db_session, test_event, s95)
    nobody = _user(db_session, "Без привязок")
    db_session.flush()

    totals = total_runs_for_users(db_session, [runner.id, nobody.id])
    assert totals == {runner.id: 2}
    assert compute_dashboard_stats(db_session, runner.id)["total_runs"] == 2


def test_fresh_cabinet_cache_wins_and_stale_one_is_recounted(db_session: Session) -> None:
    runner = _public_user(db_session, "Кэшированный")
    participant = _participant(db_session, runner)
    _history(db_session, _location(db_session, started=False), participant, 3)
    _cache_runs(db_session, runner, 42)
    assert total_runs_for_users(db_session, [runner.id]) == {runner.id: 42}

    cache = db_session.get(DashboardCache, runner.id)
    assert cache is not None
    cache.computed_at = datetime.now(UTC) - timedelta(days=3)
    db_session.flush()
    assert total_runs_for_users(db_session, [runner.id]) == {runner.id: 3}
    cache.computed_at = datetime.now(UTC)
    cache.stats = {"total_runs": 42, "analytics": {"analytics_version": ANALYTICS_VERSION - 1}}
    db_session.flush()
    assert total_runs_for_users(db_session, [runner.id]) == {runner.id: 3}


def test_card_numbers_without_cache_are_the_cabinet_numbers(db_session: Session) -> None:
    """Три числа карточки без кэша кабинета — теми же функциями, что кабинет:
    дубль зеркала не считается ни в пробежках, ни в волонтёрствах, две роли в
    один день 5 вёрст — одно волонтёрство, серия — не локация. И ничего не
    пишется: кэш кабинета карточка не трогает."""
    runner = _public_user(db_session, "Сверка")
    five_verst = _participant(db_session, runner)
    s95 = _participant(db_session, runner, "s95")
    runpark = _participant(db_session, runner, "runpark")
    s95_park = _location(db_session, "s95", started=False)
    mirror_park = _location(db_session, "runpark", started=False)
    primary = _event(db_session, s95_park, PAST_SAT, 30)
    secondary = _event(db_session, mirror_park, PAST_SAT, 12)
    db_session.add(EventCrosslink(primary_event_id=primary.id, secondary_event_id=secondary.id))
    _run(db_session, primary, s95)
    _run(db_session, secondary, runpark)
    _volunteer(db_session, primary, s95)
    _volunteer(db_session, secondary, runpark)
    _history(db_session, _location(db_session, started=False), five_verst, 3)
    helped = _event(db_session, _location(db_session, started=False), PAST_SAT - timedelta(weeks=8), 5)
    _volunteer(db_session, helped, five_verst)
    _volunteer(db_session, helped, five_verst)
    series = _location(db_session, started=False)
    series.is_series = True
    _run(db_session, _event(db_session, series, PAST_SAT - timedelta(weeks=9), 1), five_verst)
    db_session.flush()

    cabinet = compute_dashboard_stats(db_session, runner.id)
    analytics = cabinet["analytics"]
    assert isinstance(analytics, dict)
    expected = {
        "total_runs": cabinet["total_runs"],
        "total_volunteering": cabinet["total_volunteering"],
        "unique_locations": analytics["unique_locations"],
    }
    assert expected == {"total_runs": 5, "total_volunteering": 2, "unique_locations": 4}
    assert cabinet_headline_stats(db_session, runner.id) == expected
    assert db_session.get(DashboardCache, runner.id) is None
    assert cabinet_headline_stats(db_session, _user(db_session, "Без привязок").id) is None


def test_card_numbers_take_a_fresh_cabinet_cache(db_session: Session) -> None:
    runner = _public_user(db_session, "Кэш карточки")
    _history(db_session, _location(db_session, started=False), _participant(db_session, runner), 3)
    _cache_runs(db_session, runner, 42, volunteering=7, locations=11)
    assert cabinet_headline_stats(db_session, runner.id) == {
        "total_runs": 42,
        "total_volunteering": 7,
        "unique_locations": 11,
    }
    cache = db_session.get(DashboardCache, runner.id)
    assert cache is not None
    # Просрочен — профиль пересчитает кабинет, и карточка считает так же.
    cache.computed_at = datetime.now(UTC) - timedelta(days=3)
    db_session.flush()
    recounted = {"total_runs": 3, "total_volunteering": 0, "unique_locations": 1}
    assert cabinet_headline_stats(db_session, runner.id) == recounted
    cache.computed_at = datetime.now(UTC)
    cache.stats = {"total_runs": 42, "analytics": {"analytics_version": ANALYTICS_VERSION - 1}}
    db_session.flush()
    assert cabinet_headline_stats(db_session, runner.id) == recounted


# ---------------------------------------------------------------- блок локации


def test_marks_are_in_the_organizer_calendar_and_not_on_the_location_page(db_session: Session) -> None:
    """На странице локации рядом с именем только всего пробежек: «впервые у
    нас» и юбилей — в оргкабинете, чтобы блок участников был компактным
    (решение Дмитрия 25.10.2026)."""
    park = _location(db_session, name="Кузьминки", started=False)
    elsewhere = _location(db_session, name="Битца", started=False)

    regular = _public_user(db_session, "Завсегдатай", public_slug="regular")
    _history(db_session, park, _participant(db_session, regular), 9)
    newcomer = _public_user(db_session, "Новичок", public_slug="newcomer")
    _history(db_session, elsewhere, _participant(db_session, newcomer), 2)
    hundred = _public_user(db_session, "Сотый", public_slug="hundred")
    _participant(db_session, hundred)
    _cache_runs(db_session, hundred, 99)
    helper = _public_user(db_session, "Волонтёр", public_slug="helper")
    _volunteer(db_session, _event(db_session, park, PAST_SAT - timedelta(weeks=20), 1), _participant(db_session, helper))
    unlinked = _public_user(db_session, "Без привязок", public_slug="unlinked")
    for user in (regular, newcomer, hundred, helper, unlinked):
        _plan(db_session, user, park, SAT1)

    block = _people(location_plans_block(db_session, _key(park), None), SAT1)
    assert block["Завсегдатай"] == {"name": "Завсегдатай", "handle": "regular", "runs": 9, "regular": False}
    assert all(set(person) == {"name", "handle", "runs", "regular"} for person in block.values())

    people = _calendar_people(db_session, park, SAT1)
    assert people["Завсегдатай"] == {
        "name": "Завсегдатай",
        "handle": "regular",
        "runs": 9,
        "first_here": False,
        "jubilee": 10,
        # Все девять — здесь: «10-я у нас» совпала бы с «всего» и не пишется.
        "marks": [{"kind": "jubilee", "label": "10-я пробежка"}],
    }
    assert (people["Новичок"]["runs"], people["Новичок"]["first_here"], people["Новичок"]["jubilee"]) == (2, True, None)
    assert (people["Сотый"]["runs"], people["Сотый"]["jubilee"]) == (99, 100)
    # Волонтёрил здесь — уже не «впервые», хоть и не бегал.
    assert (people["Волонтёр"]["runs"], people["Волонтёр"]["first_here"]) == (0, False)
    # Привязок нет — про пробежки сказать нечего: ни числа, ни меток.
    assert (people["Без привязок"]["runs"], people["Без привязок"]["first_here"]) == (None, False)


def test_later_plans_count_earlier_ones(db_session: Session) -> None:
    """Два плана подряд: юбилей — у первой пробежки, «впервые» — у первой даты сюда."""
    park = _location(db_session, name="Кузьминки", started=False)
    elsewhere = _location(db_session, name="Царицыно", started=False)
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    _history(db_session, elsewhere, _participant(db_session, _user(db_session, "Опора-2")), 1)
    first = _public_user(db_session, "Двойной")
    _participant(db_session, first)
    _cache_runs(db_session, first, 98)
    _plan(db_session, first, elsewhere, SAT1)
    _plan(db_session, first, park, SAT2)
    twice = _public_user(db_session, "Дважды")
    _participant(db_session, twice)
    _cache_runs(db_session, twice, 99)
    _plan(db_session, twice, park, SAT1)
    _plan(db_session, twice, park, SAT2)

    first_day, second_day = _calendar_people(db_session, park, SAT1), _calendar_people(db_session, park, SAT2)
    # 98 + план на SAT1 в Царицыне → на SAT2 здесь будет 100-я.
    assert second_day["Двойной"]["jubilee"] == 100
    assert second_day["Двойной"]["first_here"] is True
    # 99 → 100-я на SAT1, а на SAT2 уже 101-я и не впервые.
    assert (first_day["Дважды"]["jubilee"], first_day["Дважды"]["first_here"]) == (100, True)
    assert (second_day["Дважды"]["jubilee"], second_day["Дважды"]["first_here"]) == (None, False)


def test_private_plan_elsewhere_does_not_shift_numbers(db_session: Session) -> None:
    """Приватная поездка в другую локацию в поправку не идёт: иначе «будет
    100-я» и «что даст визит» у организатора выдавали бы её (ревью
    27.10.2026, PRIV-7). Та же поездка публично — сдвигает."""
    park = _place(db_session, "Кузьминки")
    elsewhere = _place(db_session, "Бор", city="Тверь", region="Тверская область")
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    traveller = _public_user(db_session, "Тайный", public_slug="secret-trip")
    _participant(db_session, traveller)
    _cache_runs(db_session, traveller, 98)
    trip = _plan(db_session, traveller, elsewhere, SAT1, is_public=False)
    _plan(db_session, traveller, park, SAT2)
    db_session.flush()

    def seen() -> tuple[list[date], int | None, list[str], list[str]]:
        ahead = planner_guests_service.plans_ahead(db_session, [traveller.id], today=TODAY)[traveller.id]
        card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "secret-trip")
        return (
            [plan.plan_date for plan in ahead.plans],
            _calendar_people(db_session, park, SAT2)["Тайный"]["jubilee"],
            [item["label"] for item in card["jubilees"]],
            [item["text"] for item in card["challenges"] if item["title"] == "Новая локация"],
        )

    assert seen() == ([SAT2], None, [], ["Станет 1-й локацией в «Коллекционере локаций»"])
    trip.is_public = True
    db_session.flush()
    dates, jubilee, labels, texts = seen()
    assert (dates, jubilee, texts) == ([SAT1, SAT2], 100, ["Станет 2-й локацией в «Коллекционере локаций»"])
    assert any("100" in label for label in labels)


def test_private_and_children_are_not_counted(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Цифры считаются только у названных по имени: у приватного и ребёнка их
    нет даже в запросах к базе."""
    park = _location(db_session, started=False)
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    public = _public_user(db_session, "Открытый")
    _participant(db_session, public)
    hidden = _user(db_session, "Скрытый")
    _participant(db_session, hidden)
    child = _user(db_session, "Ребёнок")
    _run(db_session, _event(db_session, _location(db_session), PAST_SAT, 3), _participant(db_session, child), age="М10")
    for user in (public, hidden, child):
        _plan(db_session, user, park, SAT1, is_public=user is not hidden)

    asked: list[set[object]] = []
    original = planner_guests_service.total_runs_for_users

    def spy(db: Session, user_ids: list[object]) -> dict[object, int]:
        asked.append(set(user_ids))
        return original(db, user_ids)  # type: ignore[arg-type]

    monkeypatch.setattr(planner_guests_service, "total_runs_for_users", spy)
    day = location_plans_block(db_session, _key(park), None)["dates"][0]
    assert [person["name"] for person in day["people"]] == ["Открытый"]
    assert (day["private"], day["children"]) == (1, 1)
    assert asked == [{public.id}]
    # Второй просмотр страницы — число из Redis, без счёта в базе.
    location_plans_block(db_session, _key(park), None)
    assert asked == [{public.id}]


def test_block_api_shows_numbers(client: TestClient, db_session: Session) -> None:
    park = _location(db_session, started=False)
    runner = _public_user(db_session, "Бегун", public_slug="runner-api")
    _history(db_session, park, _participant(db_session, runner), 24)
    _plan(db_session, runner, park, SAT1)
    db_session.commit()
    response = client.get("/api/planner/location", params={"key": _key(park)})
    assert response.status_code == 200, response.text
    [person] = response.json()["dates"][0]["people"]
    # 25-я пробежка впереди, но на странице локации юбилея нет — только число.
    assert person == {"name": "Бегун", "handle": "runner-api", "runs": 24, "regular": False}


def test_people_go_by_runs_most_first(client: TestClient, db_session: Session) -> None:
    """Больше пробежек — выше (решение Дмитрия 25.10.2026), без привязок — в
    конце, при равенстве — кто раньше отметился. Так на странице локации, в
    оргкалендаре и в «Кто у нас бежит»."""
    park = _location(db_session, name="Кузьминки")
    noted = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    for index, (name, runs) in enumerate(
        (("Без привязок", None), ("Мало", 3), ("Много", 50), ("Тоже много", 50), ("Больше всех", 200))
    ):
        user = _public_user(db_session, name, public_slug=f"by-runs-{index}")
        if runs is not None:
            _participant(db_session, user)
            _cache_runs(db_session, user, runs)
        _plan(db_session, user, park, SAT1, created_at=noted + timedelta(minutes=index))
    db_session.commit()
    expected = ["Больше всех", "Много", "Тоже много", "Мало", "Без привязок"]

    block = client.get("/api/planner/location", params={"key": _key(park)}).json()
    assert [person["name"] for person in block["dates"][0]["people"]] == expected
    _grant(client, db_session, park)
    calendar = client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2026-10"}).json()
    [day] = [item for item in calendar["days"] if item["date"] == SAT1.isoformat()]
    assert [person["name"] for person in day["people"]] == expected
    guests = client.get(f"/api/organizer/{park.external_key}/guests").json()
    assert [person["name"] for person in guests["dates"][0]["people"]] == expected


# ---------------------------------------------------------------- «Кто у нас бежит»


def _guests_world(db: Session) -> dict[str, object]:
    park = _place(db, "Кузьминки", region="Москва")
    away = _place(db, "Битца", city="Тверь", region="Тверская область")
    _history(db, park, _participant(db, _user(db, "Опора")), 1)
    guest = _public_user(db, "Гость Тверской", public_slug="tver-guest")
    guest_participant = _participant(db, guest)
    _history(db, away, guest_participant, 2, start=50)
    _run(db, _event(db, park, PAST_SAT - timedelta(weeks=30), 1), guest_participant)
    hidden = _user(db, "Скрытый Гость")
    _participant(db, hidden)
    child = _user(db, "Юный Гость")
    _run(db, _event(db, _location(db), PAST_SAT, 3), _participant(db, child), age="Ж10-14 (3)")
    _plan(db, guest, park, SAT1)
    _plan(db, hidden, park, SAT1, is_public=False)
    _plan(db, child, park, SAT1)
    _plan(db, guest, park, TODAY + timedelta(weeks=20) + timedelta(days=(5 - TODAY.weekday()) % 7))
    db.commit()
    return {"park": park, "away": away, "guest": guest, "hidden": hidden, "child": child}


def test_guests_access_and_only_public_people(client: TestClient, db_session: Session) -> None:
    world = _guests_world(db_session)
    park = world["park"]
    assert isinstance(park, Location)
    _login(client, db_session)
    assert client.get(f"/api/organizer/{park.external_key}/guests").status_code == 403

    _grant(client, db_session, park)
    response = client.get(f"/api/organizer/{park.external_key}/guests", params={"weeks": 12})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["location"]["name"] == "Кузьминки" and payload["weeks"] == 12
    [day] = payload["dates"]
    assert day["date"] == SAT1.isoformat() and day["kind"] == "regular"
    assert (day["total"], day["private"], day["children"]) == (3, 1, 1)
    away = world["away"]
    assert isinstance(away, Location)
    assert day["people"] == [
        {
            "name": "Гость Тверской",
            "handle": "tver-guest",
            "runs": 3,
            "runs_here": 1,
            "first_here": False,
            "jubilee": None,
            "marks": [],
            # Дом — где больше пробежек, город — у его локации.
            "home": {"name": "Битца", "slug": away.external_key, "city": "Тверь", "here": False},
            # «Челленджи»: здесь уже бегал, локация и регион не новые. ≈№2 —
            # второе число Фибоначчи (бронза) и рост ещё в трёх номерных
            # челленджах: в столбце — медаль, всё остальное — в подсказке.
            "gains": [
                {
                    "label": "🥉 Числа Фибоначчи",
                    "hint": "Числа Фибоначчи — 🥉 бронза (лёгкий уровень): ≈№2 — новое число ряда, "
                    "1 → 2 числа ряда, до серебра 1\n"
                    "Нумератор: ≈№2 — новый номер, 3 → 4 номера, до бронзы 1\n"
                    "Простые числа: ≈№2 — новое простое число, 0 → 1 простой номер, до бронзы 1\n"
                    "Индекс Уилсона: ≈№2 продолжит цепочку с №1, 1 → 2, до бронзы 1",
                },
            ],
        }
    ]
    for leaked in ("Скрытый", "Юный"):
        assert leaked not in response.text

    # Двадцать недель вперёд — второй план гостя попадает в горизонт.
    longer = client.get(f"/api/organizer/{park.external_key}/guests", params={"weeks": 24}).json()
    assert len(longer["dates"]) == 2
    assert client.get(f"/api/organizer/{park.external_key}/guests", params={"weeks": 53}).status_code == 422



def test_guest_home_chosen_by_hand_wins(client: TestClient, db_session: Session) -> None:
    world = _guests_world(db_session)
    park, guest = world["park"], world["guest"]
    assert isinstance(park, Location) and isinstance(guest, User)
    guest.home_location_key = _key(park)
    db_session.commit()
    _grant(client, db_session, park)
    [person] = client.get(f"/api/organizer/{park.external_key}/guests").json()["dates"][0]["people"]
    assert person["home"] == {"name": "Кузьминки", "slug": park.external_key, "city": "Москва", "here": True}


# ---------------------------------------------------------------- страница гостя


def test_guest_card_only_for_public_plan_here(client: TestClient, db_session: Session) -> None:
    world = _guests_world(db_session)
    park = world["park"]
    assert isinstance(park, Location)
    other = _location(db_session, started=False)
    _history(db_session, other, _participant(db_session, _user(db_session, "Опора-3")), 1)
    elsewhere_only = _public_user(db_session, "Едет не к нам", public_slug="not-ours")
    _participant(db_session, elsewhere_only)
    _plan(db_session, elsewhere_only, other, SAT1)
    closed = _public_user(db_session, "Закрытый Гость", public_slug="closed-guest", profile_private=True)
    _plan(db_session, closed, park, SAT1)
    db_session.commit()
    _login(client, db_session)
    assert client.get(f"/api/organizer/{park.external_key}/guests/tver-guest").status_code == 403

    _grant(client, db_session, park)
    hidden, child = world["hidden"], world["child"]
    assert isinstance(hidden, User) and isinstance(child, User)
    for handle in ("not-ours", "closed-guest", str(hidden.serial_id), str(child.serial_id), "nobody-here", "999999999"):
        response = client.get(f"/api/organizer/{park.external_key}/guests/{handle}")
        assert response.status_code == 404, handle
        assert response.json()["detail"] == "Участник не найден"
    assert client.get(f"/api/organizer/{park.external_key}/guests/tver-guest").status_code == 200


def test_guests_and_card_follow_each_plan_publicity(client: TestClient, db_session: Session) -> None:
    """Публичность — у каждого плана (27.10.2026): приватная суббота того же
    человека в «Кто у нас бежит» — числом, в карточке её нет; только приватный
    план сюда — карточки нет, даже если публичный план есть в другом месте."""
    world = _guests_world(db_session)
    park, guest = world["park"], world["guest"]
    assert isinstance(park, Location) and isinstance(guest, User)
    _plan(db_session, guest, park, SAT2, is_public=False)
    other = _location(db_session, started=False)
    _history(db_session, other, _participant(db_session, _user(db_session, "Опора-4")), 1)
    mixed = _public_user(db_session, "Публичный не у нас", public_slug="mixed-guest")
    _participant(db_session, mixed)
    _plan(db_session, mixed, other, SAT1)
    _plan(db_session, mixed, park, SAT2, is_public=False)
    db_session.commit()
    _login(client, db_session)
    _grant(client, db_session, park)

    dates = {day["date"]: day for day in client.get(f"/api/organizer/{park.external_key}/guests").json()["dates"]}
    sat2 = dates[SAT2.isoformat()]
    assert (sat2["total"], sat2["people"], sat2["private"]) == (2, [], 2)
    card = client.get(f"/api/organizer/{park.external_key}/guests/tver-guest")
    assert card.status_code == 200, card.text
    assert [item["date"] for item in card.json()["planned_dates"]] == [
        SAT1.isoformat(),
        (TODAY + timedelta(weeks=20) + timedelta(days=(5 - TODAY.weekday()) % 7)).isoformat(),
    ]
    assert client.get(f"/api/organizer/{park.external_key}/guests/mixed-guest").status_code == 404


def test_guest_card_stats_jubilees_and_challenges(client: TestClient, db_session: Session) -> None:
    park = _place(db_session, "Кузьминки", region="Москва")
    away = _place(db_session, "Битца", city="Тверь", region="Тверская область")
    regular = _public_user(db_session, "Завсегдатай", public_slug="regular-card")
    participant = _participant(db_session, regular)
    # Девять пробежек здесь (№1…№9, последняя — в PAST_SAT) и волонтёрство:
    # ближайший старт ≈№10, он станет десятой пробежкой здесь.
    events = _history(db_session, park, participant, 9)
    _volunteer(db_session, _event(db_session, park, PAST_SAT - timedelta(weeks=12), 77), participant)
    _run(db_session, _event(db_session, away, PAST_SAT - timedelta(weeks=3), 50), participant)
    newcomer = _public_user(db_session, "Новичок", public_slug="newcomer-card")
    _run(db_session, _event(db_session, away, PAST_SAT, 51), _participant(db_session, newcomer))
    for user in (regular, newcomer):
        _plan(db_session, user, park, SAT1)
    _plan(db_session, regular, park, SAT2)
    db_session.commit()
    _grant(client, db_session, park)

    response = client.get(f"/api/organizer/{park.external_key}/guests/regular-card")
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["location"]["name"] == "Кузьминки"
    assert card["person"] == {"name": "Завсегдатай", "handle": "regular-card"}
    assert [item["date"] for item in card["planned_dates"]] == [SAT1.isoformat(), SAT2.isoformat()]
    assert card["stats"] == {
        "runs": 10,
        "volunteering": 1,
        "locations": 2,
        "runs_here": 9,
        "volunteering_here": 1,
        "last_here": events[-1].event_date.isoformat(),
        "best_here_sec": 1500,
        "first_here": False,
    }
    assert card["home"] == {"name": "Кузьминки", "slug": park.external_key, "city": "Москва", "here": True}
    # Две даты сюда — у юбилея подписана дата: десятой здесь станет первая.
    assert card["jubilees"] == [{"label": f"Будет 10-я пробежка здесь — {day_label(SAT1)}"}]
    # Номер ≈№10 ещё не брал (брал №1…№9 и №50): цепочка Уилсона №1–№9
    # дорастёт до десяти — серебро, и +1 в «Нумераторе»; локация, регион,
    # буква и система — уже есть. Десятой пробежкой ≈№10 не станет (их уже
    # десять) — «Совпадения номеров» нет.
    assert card["challenges_date"] == SAT1.isoformat()
    assert card["challenges"] == [
        {
            "title": "Индекс Уилсона — 🥈 серебро (лёгкий уровень)",
            "text": "≈№10 продолжит цепочку с №1, 9 → 10, до золота 5",
        },
        {"title": "Нумератор: ≈№10 — новый номер", "text": "10 → 11 номеров, до серебра 4"},
    ]
    assert card["challenges_note"] is None

    fresh = client.get(f"/api/organizer/{park.external_key}/guests/newcomer-card").json()
    assert fresh["stats"]["first_here"] is True and fresh["stats"]["runs_here"] == 0
    assert fresh["stats"]["last_here"] is None and fresh["stats"]["best_here_sec"] is None
    assert fresh["jubilees"] == []
    assert fresh["challenges"] == [
        {"title": "Новая локация", "text": "Станет 2-й локацией в «Коллекционере локаций»"},
        {"title": "Новый регион", "text": "Москва — станет 2-м в «Путешественнике»"},
        {"title": "Алфавит: буква «К»", "text": "Новая буква — станет 2-й"},
        {"title": "Нумератор: ≈№10 — новый номер", "text": "1 → 2 номера, до бронзы 3"},
    ]


def test_guest_card_jubilee_in_total_and_in_the_system(db_session: Session) -> None:
    """«Всего» — как в кабинете, «в системе» — как в «Календаре юбилеев»:
    у каждой системы своя юбилейная арифметика."""
    park = _location(db_session, started=False)
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    runner = _public_user(db_session, "Сотый", public_slug="hundredth")
    five_verst = _participant(db_session, runner)
    _participant(db_session, runner, "s95")
    # Кабинет: 99 пробежек во всех системах; в 5 вёрстах — 49 финишей.
    _cache_runs(db_session, runner, 99)
    _history(db_session, _location(db_session, started=False), five_verst, 49, start=100)
    _plan(db_session, runner, park, SAT1)
    db_session.flush()
    identity = SimpleNamespace(
        identity_key=_key(park), locations=[(park, "five_verst")], slug=park.external_key, name=park.name
    )
    card = planner_guests_service.organizer_guest_card(db_session, identity, "hundredth")
    assert card["stats"]["runs"] == 99
    assert card["jubilees"] == [
        {"label": "Будет 100-я пробежка"},
        {"label": "Будет 50-я пробежка в системе 5 вёрст"},
    ]
    assert card["jubilees_note"] is None


def test_guest_card_jubilee_on_a_later_planned_date(db_session: Session) -> None:
    """Юбилей не у ближайшей даты: 248 пробежек, планы на две субботы — 250-й
    станет вторая. В списке «Кто у нас бежит» метка стоит на второй дате, и
    страница участника говорит то же, с датой (до правки было «юбилеев нет»)."""
    park = _location(db_session, started=False)
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    runner = _public_user(db_session, "Двухсотпятидесятая", public_slug="two-fifty")
    _participant(db_session, runner)
    _cache_runs(db_session, runner, 248)
    _plan(db_session, runner, park, SAT1)
    _plan(db_session, runner, park, SAT2)
    db_session.flush()
    identity = SimpleNamespace(
        identity_key=_key(park), locations=[(park, "five_verst")], slug=park.external_key, name=park.name
    )
    card = planner_guests_service.organizer_guest_card(db_session, identity, "two-fifty")
    assert card["jubilees"] == [{"label": f"Будет 250-я пробежка — {day_label(SAT2)}"}]
    guests = planner_guests_service.organizer_guests(db_session, identity)
    jubilees = {item["date"]: [person["jubilee"] for person in item["people"]] for item in guests["dates"]}
    assert jubilees == {SAT1: [None], SAT2: [250]}
    marks = {item["date"]: [person["marks"] for person in item["people"]] for item in guests["dates"]}
    assert marks == {
        SAT1: [[{"kind": "first_here", "label": "впервые у нас"}]],
        SAT2: [[{"kind": "jubilee", "label": "250-я пробежка"}]],
    }


# ---------------------------------------------------------------- день старта


def _start_day_world(db: Session, day: date = TODAY, after: date = SAT1) -> dict[str, object]:
    """Сегодня (day) старт, протокол уже загружен. У каждого — план сюда на
    сегодня и на after, и after должна стать десятой пробежкой.

    «Пробежал»: восемь пробежек и сегодняшняя здесь (девятая). «Передумал»:
    восемь и сегодняшняя в другом месте — здесь не бывал. «Помог»: девять и
    сегодня волонтёрил здесь. «Не пришёл»: девять, сегодня строк нет, а
    протокол уже загружен — сегодняшний план не сбылся.

    TODAY — среда: чтобы сегодняшний план был датой календаря, она — особая.
    """
    if day.weekday() != 5:
        db.add(StartSpecialDate(start_date=day, title="Особый старт", platforms=["five_verst"]))
    park = _location(db, name="Кузьминки", started=False)
    other = _location(db, name="Битца", started=False)
    today_here = _event(db, park, day, 100)
    today_there = _event(db, other, day, 40)
    people: dict[str, User] = {}
    for name, before in (("Пробежал", 8), ("Передумал", 8), ("Помог", 9), ("Не пришёл", 9)):
        user = _public_user(db, name)
        participant = _participant(db, user)
        _history(db, _location(db, started=False), participant, before)
        if name == "Пробежал":
            _run(db, today_here, participant)
        elif name == "Передумал":
            _run(db, today_there, participant)
        elif name == "Помог":
            _volunteer(db, today_here, participant)
        _plan(db, user, park, day)
        _plan(db, user, park, after)
        people[name] = user
    db.flush()
    return {"park": park, "people": people, "day": day, "after": after}


def _start_day_stats(db: Session, world: dict[str, object]) -> dict[tuple[str, date], tuple[bool, int | None]]:
    park, people, day, after = world["park"], world["people"], world["day"], world["after"]
    assert isinstance(park, Location) and isinstance(people, dict)
    assert isinstance(day, date) and isinstance(after, date)
    entries = [(user.id, on_date) for user in people.values() for on_date in (day, after)]
    stats = planner_guests_service.people_stats(
        db, identity_key=_key(park), location_ids=[park.id], entries=entries, today=day
    )
    names = {user.id: name for name, user in people.items()}
    return {(names[user_id], on_date): (item.first_here, item.jubilee) for (user_id, on_date), item in stats.items()}


# Обычная суббота (старт у человека один) и особый день (планы снимаются поштучно).
START_DAYS = [pytest.param(SAT1, SAT2, id="saturday"), pytest.param(TODAY, SAT1, id="special-day")]


@pytest.mark.parametrize(("day", "after"), START_DAYS)
def test_start_day_plan_in_protocol_is_not_counted_twice(db_session: Session, day: date, after: date) -> None:
    """Сегодняшний план человека, который уже в сегодняшнем протоколе, — не
    поправка: до правки номер на следующей дате до полуночи был на единицу
    больше (11-я вместо 10-й), а «Передумал» там был «не впервые». Протокол
    загружен без человека — не пришёл, и план тоже не поправка: до правки у
    «Не пришёл» сегодня стояло «впервые у нас», а after была 11-й."""
    stats = _start_day_stats(db_session, _start_day_world(db_session, day, after))
    assert stats[("Пробежал", after)] == (False, 10)
    assert stats[("Передумал", after)] == (True, 10)
    assert stats[("Помог", after)] == (False, 10)
    assert stats[("Не пришёл", after)] == (True, 10)
    # Сегодняшний старт прошёл — меток о нём больше нет.
    for name in ("Пробежал", "Передумал", "Помог", "Не пришёл"):
        assert stats[(name, day)] == (False, None)


@pytest.mark.parametrize(("day", "after"), START_DAYS)
def test_start_day_number_does_not_wait_for_the_cabinet_cache(db_session: Session, day: date, after: date) -> None:
    """Кэш кабинета ещё без сегодняшней пробежки (прогрев после синка не
    дошёл): номер считается от протоколов, а не от отставшего «всего»."""
    world = _start_day_world(db_session, day, after)
    people = world["people"]
    assert isinstance(people, dict)
    _cache_runs(db_session, people["Пробежал"], 8)
    _cache_runs(db_session, people["Передумал"], 8)
    stats = _start_day_stats(db_session, world)
    assert stats[("Пробежал", after)] == (False, 10)
    assert stats[("Передумал", after)] == (True, 10)


def test_jubilee_does_not_wait_for_the_cabinet_cache_without_a_plan_today(db_session: Session) -> None:
    """Турист пробежал утром дома без плана на сегодня, протокол лёг, а кэш
    кабинета ещё старый (прогрев после синка не дошёл): метка «10-я» на его
    поездке стоит сразу, а не после прогрева — номер и от протоколов (ревью
    28.10.2026, TOT-1). Число у имени — из кэша, как у плитки профиля."""
    park = _location(db_session, started=False)
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    runner = _public_user(db_session, "Турист", public_slug="tourist-ten")
    _history(db_session, _location(db_session, started=False), _participant(db_session, runner), 9)
    _cache_runs(db_session, runner, 8)
    _plan(db_session, runner, park, SAT1)
    db_session.flush()

    stats = planner_guests_service.people_stats(
        db_session, identity_key=_key(park), location_ids=[park.id], entries=[(runner.id, SAT1)], today=TODAY
    )
    assert (stats[(runner.id, SAT1)].runs, stats[(runner.id, SAT1)].jubilee) == (8, 10)
    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "tourist-ten")
    assert card["jubilees"] == [{"label": "Будет 10-я пробежка"}]


def test_start_day_guest_card(db_session: Session) -> None:
    """Страница участника в день старта — то же правило: юбилей у SAT1, а
    «впервые» и «что даст визит» — о следующем визите, не о сегодняшнем,
    который уже в протоколе (до правки — сегодняшний: «нового не даст» у
    «Пробежал» и «новая локация» за визит, которого не было, у «Передумал»)."""
    world = _start_day_world(db_session)
    park = world["park"]
    people = world["people"]
    assert isinstance(park, Location) and isinstance(people, dict)
    for user in people.values():
        user.public_slug = f"start-day-{user.serial_id}"
    db_session.flush()
    identity = SimpleNamespace(
        identity_key=_key(park), locations=[(park, "five_verst")], slug=park.external_key, name=park.name
    )

    def card(name: str) -> dict[str, object]:
        return planner_guests_service.organizer_guest_card(db_session, identity, people[name].public_slug)

    ran = card("Пробежал")
    assert ran["stats"]["runs"] == 9 and ran["stats"]["first_here"] is False  # type: ignore[index]
    assert ran["jubilees"] == [{"label": "Будет 10-я пробежка"}]
    # Сегодня здесь №100, SAT1 — ≈№101: у визита SAT1 свой номер, и
    # сегодняшний №100 уже в счёте (номера №1…№8 и №100 — девять).
    assert ran["challenges_date"] == SAT1
    assert [(item["title"], item["text"]) for item in ran["challenges"]] == [  # type: ignore[union-attr]
        ("Простые числа — 🥈 серебро (лёгкий уровень)", "≈№101 — новое простое число, 4 → 5 простых номеров, до золота 5"),
        ("Нумератор: ≈№101 — новый номер", "9 → 10 номеров, до серебра 5"),
    ]
    changed = card("Передумал")
    assert changed["stats"]["first_here"] is True  # type: ignore[index]
    assert changed["jubilees"] == [{"label": "Будет 10-я пробежка"}]
    assert changed["challenges_date"] == SAT1
    missed = card("Не пришёл")
    assert missed["stats"]["first_here"] is True  # type: ignore[index]
    assert (missed["jubilees"], missed["jubilees_note"]) == ([{"label": "Будет 10-я пробежка"}], None)
    # Не пришёл — сегодняшний старт прошёл без него, речь о следующем визите.
    assert missed["challenges_date"] == SAT1


def test_start_day_plan_waits_for_the_protocol_of_its_location(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Суббота: протокол парка загружен без человека — не пришёл, план не
    сбылся, следующая дата — runs + 1. Протокол другого парка ещё не загружен —
    его старт впереди, и план там — ещё пробежка. До правки план неявки
    считался пробежкой до полуночи: SAT2 получала «100-я» при настоящей 99-й,
    а сегодня стояло «впервые у нас» о прошедшем старте."""
    loaded = _place(db_session, "Уже загружен")
    waiting_park = _place(db_session, "Ещё грузится")
    anchor = _participant(db_session, _user(db_session, "Опора"))
    _history(db_session, loaded, anchor, 3)
    _history(db_session, waiting_park, anchor, 3, start=10)
    _run(db_session, _event(db_session, loaded, SAT1, 4), anchor)
    no_show = _planner(db_session, "Не пришёл", loaded, SAT1, SAT2, cached=99)
    waiting = _planner(db_session, "Ждёт протокола", waiting_park, SAT1, SAT2, cached=98)
    assert no_show.public_slug is not None
    for target in (
        "app.services.start_calendar_service.moscow_today",
        "app.services.run_plan_service.moscow_today",
        "app.services.planner_guests_service.moscow_today",
    ):
        monkeypatch.setattr(target, lambda: SAT1)

    def stats(location: Location, user: User, on_date: date) -> planner_guests_service.PersonStats:
        return planner_guests_service.people_stats(
            db_session, identity_key=_key(location), location_ids=[location.id], entries=[(user.id, on_date)], today=SAT1
        )[(user.id, on_date)]

    today_row = stats(loaded, no_show, SAT1)
    assert (today_row.first_here, today_row.jubilee, _marks(today_row)) == (False, None, [])
    later = stats(loaded, no_show, SAT2)
    assert (later.jubilee, _marks(later)[0]) == (100, {"kind": "jubilee", "label": "100-я пробежка"})
    # Протокола ещё нет: 98 + сегодняшний план + SAT2 — сотая.
    assert stats(waiting_park, waiting, SAT2).jubilee == 100
    assert stats(waiting_park, waiting, SAT1).first_here is True

    card = planner_guests_service.organizer_guest_card(db_session, _identity(loaded), no_show.public_slug)
    assert card["jubilees"] == [{"label": "Будет 100-я пробежка"}]
    assert card["jubilees_note"] is None
    assert card["challenges_date"] == SAT2


def test_special_day_no_show_is_settled_too(db_session: Session) -> None:
    """Особый день: протокол локации плана загружен, а человека нет ни в одном
    протоколе — план не сбылся. До правки поштучное правило работало только
    для тех, кто есть в каком-нибудь сегодняшнем протоколе."""
    db_session.add(StartSpecialDate(start_date=TODAY, title="Особый старт", platforms=["five_verst"]))
    loaded = _place(db_session, "Уже прошёл")
    park = _place(db_session, "Кузьминки")
    anchor = _participant(db_session, _user(db_session, "Опора"))
    _history(db_session, park, anchor, 2)
    _run(db_session, _event(db_session, loaded, TODAY, 70), anchor)
    runner = _planner(db_session, "Неявка в праздник", park, SAT1, cached=99)
    _plan(db_session, runner, loaded, TODAY)
    assert _stats_on(db_session, park, runner, SAT1).jubilee == 100


def _identity(location: Location, code: str = "five_verst", **extra: object) -> object:
    fields: dict[str, object] = {
        "identity_key": _key(location),
        "locations": [(location, code)],
        "slug": location.external_key,
        "name": location.name,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _stats_on(db: Session, location: Location, user: User, on_date: date) -> planner_guests_service.PersonStats:
    return planner_guests_service.people_stats(
        db, identity_key=_key(location), location_ids=[location.id], entries=[(user.id, on_date)], today=TODAY
    )[(user.id, on_date)]


# ---------------------------------------------------------------- поправка: планы, а не даты


def test_several_plans_on_a_special_day_count_one_by_one(db_session: Session) -> None:
    """Особая дата разрешает три плана — и это три пробежки: до правки они шли
    за одну. 96 пробежек и три плана 22.10 — 24.10 станет 100-й (было «98-я»);
    99 пробежек — какой из трёх стартов 22.10 станет 100-м, не известно, и
    «будет 100-я» не ставится ни на один (было — на все три разом). Праздник —
    назавтра: и он, и суббота — среди трёх ближайших стартов."""
    holiday = TODAY + timedelta(days=1)
    db_session.add(StartSpecialDate(start_date=holiday, title="Особый старт", platforms=["five_verst"]))
    after = SAT1
    park = _place(db_session, "Кузьминки")
    holiday_parks = [_place(db_session, name) for name in ("Аннино", "Бутово", "Вешняки")]
    people = {}
    for name, runs in (("Девяносто шесть", 96), ("Девяносто девять", 99)):
        user = _public_user(db_session, name, public_slug=f"special-{runs}")
        _participant(db_session, user)
        _cache_runs(db_session, user, runs)
        for place in holiday_parks:
            _plan(db_session, user, place, holiday)
        _plan(db_session, user, park, after)
        people[runs] = user

    assert _stats_on(db_session, park, people[96], after).jubilee == 100
    assert _stats_on(db_session, park, people[99], after).jubilee is None
    for place in holiday_parks:
        assert _stats_on(db_session, place, people[99], holiday).jubilee is None
    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "special-96")
    assert card["jubilees"] == [{"label": "Будет 100-я пробежка"}]
    holiday_card = planner_guests_service.organizer_guest_card(db_session, _identity(holiday_parks[0]), "special-99")
    assert holiday_card["jubilees"] == []


def test_special_day_plans_settle_one_by_one(db_session: Session) -> None:
    """Особый день: утром человек пробежал в одном месте — дневной план в
    другом ещё впереди, пока протокол той локации не загружен. До правки
    первая же строка в сегодняшнем протоколе снимала все планы дня: у
    дневного старта пропадали «впервые» и «будет 10-я», а следующая дата
    получала 10-ю вместо 11-й."""
    db_session.add(StartSpecialDate(start_date=TODAY, title="Особый старт", platforms=["five_verst"]))
    morning = _place(db_session, "Утренний парк")
    afternoon = _place(db_session, "Дневной парк")
    loaded = _place(db_session, "Уже прошёл")
    park = _place(db_session, "Кузьминки")
    morning_start = _event(db_session, morning, TODAY, 50)
    _event(db_session, loaded, TODAY, 70)
    for name in ("Ждёт дневной", "Пропустил второй"):
        user = _public_user(db_session, name)
        participant = _participant(db_session, user)
        _history(db_session, _place(db_session, f"История {name}"), participant, 8)
        _run(db_session, morning_start, participant)  # девятая
        _plan(db_session, user, morning, TODAY)
        _plan(db_session, user, afternoon if name == "Ждёт дневной" else loaded, TODAY)
        _plan(db_session, user, park, SAT1)
        if name == "Ждёт дневной":
            waiting = user
        else:
            missed = user

    # Дневной старт впереди: десятая пробежка и впервые здесь.
    later_today = _stats_on(db_session, afternoon, waiting, TODAY)
    assert (later_today.first_here, later_today.jubilee) == (True, 10)
    assert _stats_on(db_session, park, waiting, SAT1).jubilee is None  # 11-я
    # Протокол второй локации загружен без него — план не сбылся: SAT1 — 10-я.
    assert _stats_on(db_session, loaded, missed, TODAY).jubilee is None
    assert _stats_on(db_session, park, missed, SAT1).jubilee == 10


def test_plan_on_a_cancelled_start_is_not_an_earlier_run(db_session: Session) -> None:
    """Отмена план не удаляет, но пробежки не будет: поправка его не считает.
    До правки у 99 пробежек и плана на отменённую субботу в другом месте SAT2
    была «101-я» вместо 100-й, а отменённый план сюда гасил «впервые»."""
    cancelled = _place(db_session, "Отменили")
    cancelled.is_cancelled = True
    park = _place(db_session, "Кузьминки")
    db_session.flush()
    elsewhere = _public_user(db_session, "Отмена там")
    _participant(db_session, elsewhere)
    _cache_runs(db_session, elsewhere, 99)
    _plan(db_session, elsewhere, cancelled, SAT1)
    _plan(db_session, elsewhere, park, SAT2)

    here_cancelled = _place(db_session, "Отменили у нас")
    here_cancelled.is_cancelled = True
    db_session.flush()
    here = _public_user(db_session, "Отмена здесь")
    _participant(db_session, here)
    _cache_runs(db_session, here, 3)
    _plan(db_session, here, here_cancelled, SAT1)
    _plan(db_session, here, here_cancelled, SAT2)

    assert _stats_on(db_session, park, elsewhere, SAT2).jubilee == 100
    # Сам отменённый старт — без меток: предсказывать нечего.
    assert _stats_on(db_session, here_cancelled, here, SAT1) == planner_guests_service.PersonStats(
        runs=3, runs_here=0, first_here=False, jubilee=None
    )
    assert _stats_on(db_session, here_cancelled, here, SAT2).first_here is True


def test_plan_on_a_location_gone_on_pause_is_not_an_earlier_run(db_session: Session) -> None:
    """Локация ушла на паузу после того, как человек отметил субботу: календарь
    у неё пуст, пробежки не будет — поправка план не считает, и у самой
    локации меток нет. До правки суббота «была в календаре всегда»: у 99
    пробежек SAT2 выходила 101-й, и настоящая «100-я пробежка» терялась."""
    paused = _place(db_session, "На паузе")
    park = _place(db_session, "Кузьминки")
    anchor = _participant(db_session, _user(db_session, "Опора"))
    _history(db_session, paused, anchor, 2)
    _history(db_session, park, anchor, 2, start=10)
    runner = _planner(db_session, "Пауза", park, SAT2, cached=99)
    _plan(db_session, runner, paused, SAT1)
    assert _stats_on(db_session, park, runner, SAT2).jubilee is None  # пока жива — 101-я
    paused.is_paused = True
    db_session.flush()
    assert _stats_on(db_session, park, runner, SAT2).jubilee == 100
    there = _stats_on(db_session, paused, runner, SAT1)
    assert (there.first_here, there.jubilee, there.marks) == (False, None, ())


def test_plan_on_a_date_gone_from_the_calendar_is_not_an_earlier_run(db_session: Session) -> None:
    """Админ убрал особую дату — план на неё остался, но старта не будет."""
    gone = _place(db_session, "Праздник отменили")
    park = _place(db_session, "Кузьминки")
    runner = _public_user(db_session, "Без праздника")
    _participant(db_session, runner)
    _cache_runs(db_session, runner, 99)
    _plan(db_session, runner, gone, HOLIDAY)
    after = HOLIDAY + timedelta(days=3)
    _plan(db_session, runner, park, after)
    assert _stats_on(db_session, park, runner, after).jubilee == 100


# ---------------------------------------------------------------- без привязок, дом


def test_unlinked_guest_has_no_numbers_here(db_session: Session) -> None:
    """Привязок нет — «здесь: 0» было бы неправдой: под непривязанным
    штрихкодом у человека могут быть десятки пробежек здесь."""
    park = _place(db_session, "Кузьминки")
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    unlinked = _public_user(db_session, "Без привязок", public_slug="unlinked-guest")
    _plan(db_session, unlinked, park, SAT1)
    db_session.flush()

    [day] = planner_guests_service.organizer_guests(db_session, _identity(park))["dates"]
    [person] = day["people"]
    assert (person["runs"], person["runs_here"], person["first_here"], person["marks"]) == (None, None, False, [])
    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "unlinked-guest")
    stats = card["stats"]
    assert (stats["runs"], stats["runs_here"], stats["volunteering_here"], stats["first_here"]) == (None, None, None, False)
    assert card["challenges"] == [] and card["jubilees"] == []


def test_guest_home_here_is_the_identity_not_the_slug(db_session: Session) -> None:
    """Слаг дома (по старшинству систем) и слаг страницы (по действующей)
    бывают разными — у Раменского ramenskoegorodskoypark и ramenskoe. «Ваша
    локация» — по ключу идентичности."""
    park = _place(db_session, "Раменское")
    guest = _public_user(db_session, "Домосед", public_slug="home-here")
    _history(db_session, park, _participant(db_session, guest), 3)
    _plan(db_session, guest, park, SAT1)
    db_session.flush()
    identity = _identity(park, slug="ramenskoe")
    [day] = planner_guests_service.organizer_guests(db_session, identity)["dates"]
    assert day["people"][0]["home"] == {"name": "Раменское", "slug": park.external_key, "city": "Москва", "here": True}


# ---------------------------------------------------------------- что даст визит


def _mirror_world(db: Session) -> tuple[Location, Location, str]:
    """Точка S95 «Новгород» и её зеркало RunPark под одним узлом каталога;
    у S95 — опорный старт для ≈№. Возвращает (S95, RunPark, ключ)."""
    main = _location(db, "s95", name="Новгород")
    mirror = _location(db, "runpark", name="Новгород RunPark")
    catalog = LocationCatalog(canonical_name="Новгород", active_platform="s95")
    db.add(catalog)
    db.flush()
    for location in (main, mirror):
        db.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=location.platform_id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db.flush()
    _event(db, main, PAST_SAT, 177)
    return main, mirror, f"catalog:{catalog.id}"


def _mirror_identity(main: Location, mirror: Location, key: str) -> object:
    return SimpleNamespace(
        identity_key=key, locations=[(main, "s95"), (mirror, "runpark")], slug=main.external_key, name="Новгород"
    )


def _mirror_plan(db: Session, user: User, main: Location, key: str, on_date: date) -> None:
    """План так, как его сохраняет «Планирую»: календарь точки отдаёт S95."""
    db.add(
        RunPlan(
            user_id=user.id, plan_date=on_date, location_key=key, location_id=main.id, platform_code="s95", is_public=True
        )
    )
    db.flush()


def test_visit_gains_follow_the_linked_system_of_a_mirror(db_session: Session) -> None:
    """Точка S95 и её зеркало RunPark: у человека только RunPark — финиш
    придёт RunPark-строкой со своим номером. До правки — «Новая система: С95»
    и ≈№ по нумерации S95."""
    main, mirror, key = _mirror_world(db_session)
    runner = _public_user(db_session, "Только RunPark", public_slug="runpark-only")
    participant = _participant(db_session, runner, "runpark")
    for weeks, number in ((2, 233), (1, 234), (0, 235)):
        _run(db_session, _event(db_session, mirror, PAST_SAT - timedelta(weeks=weeks), number), participant)
    _mirror_plan(db_session, runner, main, key, SAT1)
    identity = _mirror_identity(main, mirror, key)
    card = planner_guests_service.organizer_guest_card(db_session, identity, "runpark-only")
    titles = [item["title"] for item in card["challenges"]]
    assert "Новая система" not in titles
    assert "Нумератор ПРО: ≈№236 — новый номер" in titles
    assert not any("178" in title for title in titles)


def test_system_number_of_a_mirror_counts_earlier_plans(db_session: Session) -> None:
    """Поправка в системе — по системе, которой ляжет пробежка: планы на точку
    S95 хранят s95, а у человека только с RunPark визиты лягут RunPark-строкой.
    24 финиша в RunPark и две субботы подряд — 25-я и 26-я. До правки ранние
    планы в RunPark не находились, и «25-я в RunPark» стояла на обеих датах."""
    main, mirror, key = _mirror_world(db_session)
    elsewhere = _location(db_session, "runpark", name="Другой RunPark", started=False)
    runner = _public_user(db_session, "Только RunPark", public_slug="runpark-twice")
    participant = _participant(db_session, runner, "runpark")
    for weeks in range(24):
        _run(db_session, _event(db_session, elsewhere, PAST_SAT - timedelta(weeks=weeks + 1), 100 - weeks), participant)
    _cache_runs(db_session, runner, 60)
    _mirror_plan(db_session, runner, main, key, SAT1)
    _mirror_plan(db_session, runner, main, key, SAT2)
    stats = planner_guests_service.people_stats(
        db_session,
        identity_key=key,
        location_ids=[main.id, mirror.id],
        entries=[(runner.id, SAT1), (runner.id, SAT2)],
        today=TODAY,
        rows=[(main, "s95"), (mirror, "runpark")],
    )
    assert _marks(stats[(runner.id, SAT1)]) == [
        {"kind": "jubilee_platform", "label": "25-я в RunPark"},
        {"kind": "first_here", "label": "впервые у нас"},
    ]
    assert _marks(stats[(runner.id, SAT2)]) == []
    card = planner_guests_service.organizer_guest_card(db_session, _mirror_identity(main, mirror, key), "runpark-twice")
    assert card["jubilees"] == [{"label": f"Будет 25-я пробежка в системе RunPark — {day_label(SAT1)}"}]


def test_system_number_keeps_mirror_finishes_without_a_primary(db_session: Session) -> None:
    """Финиши на зеркале RunPark — вторичная сторона кросслинка. У человека
    только с RunPark первичной (S95) нет, и в RunPark они в счёте, как и во
    «всего»: 40 на зеркале и 9 в обычном RunPark — 49, визит — 50-я и там, и
    там, и вторая метка не пишется. До правки счётчик системы выбрасывал все
    вторичные стороны: «50-я пробежка» и ложная «10-я в RunPark»."""
    main, mirror, key = _mirror_world(db_session)
    elsewhere = _location(db_session, "runpark", name="Обычный RunPark", started=False)
    runner = _public_user(db_session, "RunPark зеркало")
    participant = _participant(db_session, runner, "runpark")
    dates = [PAST_SAT - timedelta(weeks=weeks + 1) for weeks in range(41)]
    for weeks, on_date in enumerate(on_date for on_date in dates if on_date != RECENT_START):
        primary = _event(db_session, main, on_date, 176 - weeks)
        secondary = _event(db_session, mirror, on_date, 299 - weeks)
        db_session.add(EventCrosslink(primary_event_id=primary.id, secondary_event_id=secondary.id))
        db_session.flush()
        _run(db_session, secondary, participant)
    for weeks in range(9):
        _run(db_session, _event(db_session, elsewhere, PAST_SAT - timedelta(weeks=weeks + 1), 50 - weeks), participant)
    # С двумя привязками дубль выпадает: пробежка на первичной стороне есть.
    both = _user(db_session, "S95 и RunPark")
    both_s95 = _participant(db_session, both, "s95")
    both_runpark = _participant(db_session, both, "runpark")
    crosslink = db_session.query(EventCrosslink).filter(EventCrosslink.primary_event_id == primary.id).one()
    _run(db_session, primary, both_s95)
    _run(db_session, db_session.get(Event, crosslink.secondary_event_id), both_runpark)

    assert total_runs_for_users(db_session, [runner.id]) == {runner.id: 49}
    assert planner_guests_service._system_runs(db_session, [runner.id, both.id]) == {
        runner.id: {"runpark": 49},
        both.id: {"s95": 1, "runpark": 0},
    }
    _mirror_plan(db_session, runner, main, key, SAT1)
    stats = planner_guests_service.people_stats(
        db_session,
        identity_key=key,
        location_ids=[main.id, mirror.id],
        entries=[(runner.id, SAT1)],
        today=TODAY,
        rows=[(main, "s95"), (mirror, "runpark")],
    )
    assert _marks(stats[(runner.id, SAT1)]) == [{"kind": "jubilee", "label": "50-я пробежка"}]


def test_visit_gains_count_earlier_plans(db_session: Session) -> None:
    """Порядковые номера «что даст визит» — с поправкой на более ранние планы,
    как и юбилеи: сбудется суббота в «Бору» — «Вега» станет третьей локацией,
    а Тверская область уже не новая."""
    home = _place(db_session, "Парк у дома", region="Москва")
    bor = _place(db_session, "Бор", city="Тверь", region="Тверская область")
    vega = _place(db_session, "Вега", city="Тверь", region="Тверская область")
    traveller = _public_user(db_session, "Путешественник", public_slug="traveller")
    _history(db_session, home, _participant(db_session, traveller), 9)
    _plan(db_session, traveller, bor, SAT1)
    _plan(db_session, traveller, vega, SAT2)
    db_session.flush()

    card = planner_guests_service.organizer_guest_card(db_session, _identity(vega), "traveller")
    assert card["challenges_date"] == SAT2
    assert card["challenges"] == [
        {
            "title": "Новая локация",
            "text": "Станет 3-й локацией в «Коллекционере локаций»",
            "short": "новая локация (3-я)",
        },
        {"title": "Алфавит: буква «В»", "text": "Новая буква — станет 3-й", "short": "буква «В»"},
    ]
    # Столбец «Челленджи» в «Кто у нас бежит» — тот же расчёт пакетом: на
    # ближайшей дате человека — то же, что на его странице.
    guests = planner_guests_service.organizer_guests(db_session, _identity(vega), 12)
    [day] = guests["dates"]
    [row] = day["people"]
    assert [gain["label"] for gain in row["gains"]] == ["новая локация (3-я)", "буква «В»"]
    assert row["gains"][0]["hint"] == "Новая локация — Станет 3-й локацией в «Коллекционере локаций»"


def test_guest_gains_only_on_horizon_and_for_regulars(db_session: Session) -> None:
    """«Челленджи» — на три ближайших старта, как юбилеи (дальше пусто), и у
    постоянных тоже — по хендлу, как метки."""
    vega = _place(db_session, "Вега", city="Тверь", region="Тверская область")
    # Локация живая (старт на прошлой неделе), а ≈№ за 400 — коллекции
    # номеров молчат, в столбце только то, что проверяем.
    _event(db_session, vega, PAST_SAT, 500)
    home = _place(db_session, "Парк у дома", region="Москва")
    regular = _public_user(db_session, "Постоянный", public_slug="gain-regular")
    _history(db_session, home, _participant(db_session, regular), 5)
    _series(db_session, regular, vega, SAT1)
    far = _public_user(db_session, "Дальний", public_slug="gain-far")
    _history(db_session, _place(db_session, "Другой дом", region="Москва"), _participant(db_session, far), 5)
    far_date = SAT1 + timedelta(weeks=6)
    _plan(db_session, far, vega, far_date)
    db_session.flush()

    guests = planner_guests_service.organizer_guests(db_session, _identity(vega), 12)
    days = {day["date"]: day for day in guests["dates"]}
    first = days[SAT1]
    assert [gain["label"] for gain in first["regulars"]["gains"]["gain-regular"]] == [
        "новая локация (2-я)",
        "новый регион",
        "буква «В»",
    ]
    # Вторая суббота постоянного: первая считается случившейся — нового нет.
    assert days[SAT1 + timedelta(weeks=1)]["regulars"]["gains"] == {}
    # За горизонтом — пусто, хотя у дальнего это первая Вега.
    [far_row] = days[far_date]["people"]
    assert far_row["gains"] == []


# ---------------------------------------------------------------- номерные челленджи


def _numbered(on_date: date, number: int | None, key: str = "catalog:kuzminki") -> RunRow:
    return RunRow(
        event_date=on_date,
        finish_time_sec=1500,
        position=None,
        event_number=number,
        location_name="Кузьминки",
        location_key=key,
        region="Москва",
        country="Россия",
        platform_code="five_verst",
        is_pr=False,
    )


def _cards(rows: list[RunRow]) -> dict[str, dict[str, object]]:
    return {
        str(card["code"]): card
        for card in _build_challenge_list(
            rows, {}, {}, [], [], [], alphabet_names={}, platform_code=None, home_country="Россия"
        )
    }


def _number_history() -> list[RunRow]:
    """28 пробежек по порядку _collect_run_rows: №1…№14 подряд (цепочка
    Уилсона и четырнадцать совпадений), два старта №100, нельсоны №111,
    №222 и №1110, номера за 400 и без номера, повторы, а в последний день —
    две пробежки (особая дата): «Совпадение номеров» раскладывает их по
    ключу локации, №27 на «a» — двадцать седьмая."""
    numbers = [*range(1, 15), 100, 100, 111, 233, 50, None, 450, 1110, 377, 13, 222, 2]
    start = date(2020, 1, 4)
    rows = [_numbered(start + timedelta(weeks=index), number) for index, number in enumerate(numbers)]
    tie = start + timedelta(weeks=len(numbers))
    return [*rows, _numbered(tie, 27, "catalog:a"), _numbered(tie, 99, "catalog:b")]


@pytest.mark.parametrize(
    ("earlier", "number"),
    [
        ([], 15),  # цепочка Уилсона 14 → 15 — золото лёгкого; «Нумератор»
        ([], 29),  # двадцать девятая пробежка на №29 и простое число
        ([], 21),  # число Фибоначчи
        ([], 250),  # юбилей и «Нумератор ПРО»
        ([], 444),  # четвёртый нельсон за золотом; за 400 — больше ничего
        ([], 233),  # уже есть везде — роста нет
        ([], 2),
        ([29, None], 31),  # ранние визиты: №29 и визит без номера — №31 тридцать первая
        ([100, 15], 16),  # третий №100 — юбилей ранних планов; цепочка 15 → 16
    ],
)
def test_number_gains_follow_the_challenge_cards(earlier: list[int | None], number: int) -> None:
    """«Что даст визит» по номерам — те же правила, что карточки челленджей:
    рост счётчика, взятые уровни и «сколько осталось» до следующего по тиру,
    который карточка покажет первой. Визит и ранние планы — пробежками в
    конце истории, сверка с _build_challenge_list."""
    rows = _number_history()
    later = rows[-1].event_date

    def planned(index: int, value: int | None) -> RunRow:
        return _numbered(later + timedelta(weeks=index + 1), value, "catalog:z")

    before_rows = [*rows, *(planned(index, value) for index, value in enumerate(earlier))]
    before = _cards(before_rows)
    after = _cards([*before_rows, planned(len(earlier), number)])
    gains = {gain.code: gain for gain in number_gains(VisitHistory.from_rows(rows), number=number, earlier=earlier)}
    for code, (title, icon, unit) in NUMBER_CHALLENGES.items():
        card = after[code]
        assert (card["title"], card["icon"], card["unit"] or "") == (title, icon, unit)
        grew = int(card["current"]) > int(before[code]["current"])  # type: ignore[call-overload]
        assert (code in gains) == grew, code
        if not grew:
            continue
        gain = gains[code]
        assert (gain.before, gain.after) == (before[code]["current"], card["current"]), code
        tiers = card["tiers"]
        assert isinstance(tiers, list)
        was = {tier["tier"]: tier["level"] for tier in before[code]["tiers"]}  # type: ignore[attr-defined]
        assert gain.ups == tuple((tier["tier"], tier["level"]) for tier in tiers if tier["level"] != was[tier["tier"]])
        shown = next(tier for tier in tiers if tier["tier"] == card["default_tier"])
        assert (gain.next_level, gain.to_next) == (shown["next_level"], shown["to_next_level"]), code


def test_number_gains_skip_the_match_when_the_day_has_more_plans() -> None:
    """Два-три плана особого дня — какой станет 29-й пробежкой, не известно:
    «Совпадение номеров» молчит, остальной рост — как обычно."""
    history = VisitHistory.from_rows(_number_history())
    assert history.runs == 28
    assert "number_match" in {gain.code for gain in number_gains(history, number=29, earlier=[])}
    codes = {gain.code for gain in number_gains(history, number=29, earlier=[], alone=False)}
    assert "number_match" not in codes and {"start_numbers", "primes"} <= codes


@pytest.mark.parametrize(
    ("numbers", "number", "short"),
    [
        # Один челлендж растёт — он с числами.
        ({201, 202, 205}, 220, "Нумератор ПРО: 3 → 4"),
        # Несколько, без уровней — сколько.
        ({2, 3, 5, 7, 11, 13, 17}, 31, "рост в 2 челленджах"),
        # Уровни — медаль первого и сколько ещё; рост без уровня — только в подсказке.
        ({1, 2, 3, 4, 5, 8, 13, 21, 34, 55, 144}, 233, "🥉 Числа Фибоначчи и ещё 1"),
    ],
)
def test_number_gains_take_one_line_in_the_table(numbers: set[int], number: int, short: str) -> None:
    """Столбец «Челленджи» узкий: номерные челленджи визита — одной строкой
    с общей подписью, полный текст — по строке на челлендж в подсказке."""
    rows = number_gain_rows(number_gains(VisitHistory(numbers=numbers, runs=40), number=number, earlier=[]))
    assert rows and {row["short"] for row in rows} == {short}
    assert len({row["hint"] for row in rows}) == len(rows)


def test_visit_histories_count_like_the_collected_rows(db_session: Session) -> None:
    """Пакетная история для столбца «Челленджи» — то же, что сбор челленджей
    одного человека: номера, юбилеи, число пробежек и «Совпадение номеров»,
    где две пробежки особого дня ложатся по ключу локации (порядок ключей
    случаен — ответ сверяется с ним же)."""
    first = _place(db_session, "Аннино")
    second = _place(db_session, "Бутово")
    runner = _public_user(db_session, "Совпадения")
    participant = _participant(db_session, runner)
    for weeks, number in ((4, 1), (3, 2)):
        _run(db_session, _event(db_session, first, PAST_SAT - timedelta(weeks=weeks), number), participant)
    # Особый день: №3 в «Аннино» и №4 в «Бутово» — третья и четвёртая
    # пробежки, совпадут обе или ни одна.
    tie = PAST_SAT - timedelta(weeks=2)
    _run(db_session, _event(db_session, first, tie, 3), participant)
    _run(db_session, _event(db_session, second, tie, 4), participant)
    _run(db_session, _event(db_session, second, PAST_SAT, 50), participant)
    other = _user(db_session, "Сосед")
    _history(db_session, _place(db_session, "Вешняки"), _participant(db_session, other), 3, start=7)
    db_session.flush()

    batch = visit_histories(db_session, [runner.id, other.id])
    in_order = _key(first) < _key(second)
    for user, runs, matches in ((runner, 5, 4 if in_order else 2), (other, 3, 0)):
        alone = VisitHistory.from_rows(_collect_run_rows(db_session, user.id))
        assert (batch[user.id].runs, batch[user.id].matches) == (alone.runs, alone.matches) == (runs, matches)
        assert (batch[user.id].numbers, batch[user.id].jubilees) == (alone.numbers, alone.jubilees)


def test_mirror_runs_of_one_day_count_in_one_order(db_session: Session) -> None:
    """Две пробежки в один день на одной точке в двух системах без
    кросслинка (зеркало RunPark): обе истории кладут их в одном полном
    порядке — ключ, система, номер, — и «Совпадение номеров» карточки не
    расходится со столбцом «Челленджи» (ревью 28.10.2026, ORACLE-1/TOT-2).
    Порядок «five_verst» < «runpark»: 2-я пробежка — №2 5 вёрст, совпадение;
    3-я — №3 RunPark, тоже."""
    main = _location(db_session, "five_verst", name="Боева дача", started=False)
    mirror = _location(db_session, "runpark", name="Боева дача RunPark", started=False)
    catalog = LocationCatalog(canonical_name="Боева дача", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    for location in (main, mirror):
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=location.platform_id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    runner = _public_user(db_session, "Зеркальный день")
    five_verst = _participant(db_session, runner)
    runpark = _participant(db_session, runner, "runpark")
    _run(db_session, _event(db_session, main, PAST_SAT - timedelta(weeks=2), 1), five_verst)
    day = PAST_SAT - timedelta(weeks=1)
    _run(db_session, _event(db_session, mirror, day, 3), runpark)
    _run(db_session, _event(db_session, main, day, 2), five_verst)
    db_session.flush()

    rows = _collect_run_rows(db_session, runner.id)
    assert [(row.platform_code, row.event_number) for row in rows] == [
        ("five_verst", 1),
        ("five_verst", 2),
        ("runpark", 3),
    ]
    alone = VisitHistory.from_rows(rows)
    batch = visit_histories(db_session, [runner.id])[runner.id]
    assert (batch.runs, batch.matches) == (alone.runs, alone.matches) == (3, 3)


def test_guest_numbers_grow_with_earlier_plans(db_session: Session) -> None:
    """Девять пробежек здесь, №1…№9, и планы на две субботы. SAT1 — ≈№10 и
    десятая пробежка: цепочка Уилсона — серебро, «Нумератор» и «Совпадение
    номеров» растут. SAT2 — ≈№11, и SAT1 уже считается случившимся: номера,
    цепочка и совпадения — от него, а 11 — пятое простое число, серебро."""
    park = _place(db_session, "Кузьминки", region="Москва")
    runner = _public_user(db_session, "Совпадающий", public_slug="match-runner")
    _history(db_session, park, _participant(db_session, runner), 9)
    _plan(db_session, runner, park, SAT1)
    _plan(db_session, runner, park, SAT2)
    db_session.flush()

    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "match-runner")
    assert card["challenges_date"] == SAT1
    assert [(item["title"], item["text"]) for item in card["challenges"]] == [
        ("Индекс Уилсона — 🥈 серебро (лёгкий уровень)", "≈№10 продолжит цепочку с №1, 9 → 10, до золота 5"),
        ("Нумератор: ≈№10 — новый номер", "9 → 10 номеров, до серебра 5"),
        ("Совпадение номеров: станет 10-й пробежкой на старте ≈№10", "9 → 10 совпадений, до золота 2"),
    ]

    guests = planner_guests_service.organizer_guests(db_session, _identity(park), 12)
    gains = {day["date"]: day["people"][0]["gains"] for day in guests["dates"]}
    # В столбце номерные визита — одной строкой: медаль, остальное — в подсказке.
    assert gains[SAT1] == [
        {
            "label": "🥈 Индекс Уилсона",
            "hint": "Индекс Уилсона — 🥈 серебро (лёгкий уровень): ≈№10 продолжит цепочку с №1, 9 → 10, до золота 5\n"
            "Нумератор: ≈№10 — новый номер, 9 → 10 номеров, до серебра 5\n"
            "Совпадение номеров: станет 10-й пробежкой на старте ≈№10, 9 → 10 совпадений, до золота 2",
        },
    ]
    [sat2] = gains[SAT2]
    assert sat2["label"] == "🥈 Простые числа"
    assert sat2["hint"].splitlines() == [
        "Простые числа — 🥈 серебро (лёгкий уровень): ≈№11 — новое простое число, 4 → 5 простых номеров, до золота 5",
        "Нумератор: ≈№11 — новый номер, 10 → 11 номеров, до серебра 4",
        "Совпадение номеров: станет 11-й пробежкой на старте ≈№11, 10 → 11 совпадений, до золота 1",
        "Индекс Уилсона: ≈№11 продолжит цепочку с №1, 10 → 11, до золота 4",
    ]

    # Страница участника говорит о второй субботе то же, что её строка в
    # таблице: организатор мог открыть её из вкладки SAT2.
    [later] = card["later_visits"]
    assert later["date"] == SAT2
    for on_date, items in ((SAT1, card["challenges"]), (SAT2, later["challenges"])):
        table = [line for gain in gains[on_date] for line in gain["hint"].splitlines()]
        assert table == [item["hint"] for item in items]


def test_guest_card_numbers_only_on_the_horizon(db_session: Session) -> None:
    """Ближайший визит — за тремя ближайшими стартами: новая локация есть, а
    номерных строк нет, и страница говорит почему."""
    park = _place(db_session, "Кузьминки", region="Москва")
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 5)
    runner = _public_user(db_session, "Дальний план", public_slug="far-numbers")
    _history(db_session, _place(db_session, "Аннино", region="Москва"), _participant(db_session, runner), 3)
    _plan(db_session, runner, park, SAT4)
    db_session.flush()

    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "far-numbers")
    assert card["challenges_date"] == SAT4
    assert [item["title"] for item in card["challenges"]] == ["Новая локация", "Алфавит: буква «К»"]
    assert card["challenges_note"] == planner_guests_service.NUMBERS_HORIZON_NOTE


# ---------------------------------------------------------------- знаковый старт и горизонт юбилеев


def _planner(db: Session, name: str, location: Location, *dates: date, cached: int | None = None) -> User:
    """Публичный человек с привязкой и планами сюда; cached — «всего» в кэше кабинета."""
    user = _public_user(db, name, public_slug=f"mark-{uuid4().hex[:8]}")
    _participant(db, user)
    if cached is not None:
        _cache_runs(db, user, cached)
    for on_date in dates:
        _plan(db, user, location, on_date)
    return user


def test_guests_show_a_landmark_start_column(client: TestClient, db_session: Session) -> None:
    """Столбец «Знаковый старт» в «Кто у нас бежит» (решение Дмитрия
    25.10.2026: юбилей виден в списке, а не только в карточке) — короткие
    подписи по правилу «Календаря юбилеев». «Первая пробежка» поглощает
    «впервые у нас»: первый старт вообще — и здесь впервые."""
    park = _place(db_session, "Кузьминки")
    away = _place(db_session, "Битца")
    here_events = [_event(db_session, park, PAST_SAT - timedelta(weeks=weeks), 99 - weeks) for weeks in range(99)]
    away_events = [_event(db_session, away, PAST_SAT - timedelta(weeks=weeks), 21 - weeks) for weeks in range(21)]

    def runner(name: str, *, here: int = 0, elsewhere: int = 0, cached: int | None = None) -> None:
        user = _public_user(db_session, name)
        participant = _participant(db_session, user)
        for event in here_events[:here] + away_events[:elsewhere]:
            _run(db_session, event, participant)
        if cached is not None:
            _cache_runs(db_session, user, cached)
        _plan(db_session, user, park, SAT1)

    runner("Дебютант")
    runner("Новичок", elsewhere=2)
    runner("Юбиляр", here=3, cached=249)
    # 99 здесь + 21 там: всего 121-я, в 5 вёрстах 121-я, а у нас — сотая.
    runner("Сотый у нас", here=99, elsewhere=21)
    # Всего 61-я, здесь 4-я, а финишей в 5 вёрстах 24 — будет 25-й.
    runner("Юбилей в системе", here=3, elsewhere=21, cached=60)
    db_session.commit()
    _grant(client, db_session, park)

    response = client.get(f"/api/organizer/{park.external_key}/guests")
    assert response.status_code == 200, response.text
    [day] = response.json()["dates"]
    people = {person["name"]: person for person in day["people"]}
    assert {name: person["marks"] for name, person in people.items()} == {
        "Дебютант": [{"kind": "first_ever", "label": "первая пробежка"}],
        "Новичок": [{"kind": "first_here", "label": "впервые у нас"}],
        "Юбиляр": [{"kind": "jubilee", "label": "250-я пробежка"}],
        "Сотый у нас": [{"kind": "jubilee_here", "label": "100-я у нас"}],
        "Юбилей в системе": [{"kind": "jubilee_platform", "label": "25-я в 5 вёрст"}],
    }
    # Поля прежнего вида на месте: юбилей «всего» и «впервые».
    assert (people["Юбиляр"]["jubilee"], people["Дебютант"]["first_here"]) == (250, True)


def test_jubilees_only_on_the_three_nearest_starts(db_session: Session) -> None:
    """Юбилеи — на три ближайших старта локации (решение Дмитрия 25.10.2026):
    дальше не поручиться, что человек пробежит всё до них. «Впервые у нас» —
    на любую дату: это не прогноз номера. «Первая пробежка» — номер, и за
    горизонтом её тоже нет."""
    park = _place(db_session, "Кузьминки")
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    third = _planner(db_session, "На третий старт", park, SAT3, cached=249)
    fourth = _planner(db_session, "На четвёртый старт", park, SAT4, cached=249)
    debut = _planner(db_session, "Дебют на четвёртый", park, SAT4)

    assert sorted(planner_guests_service.jubilee_horizon(db_session, [(park, "five_verst")], TODAY)) == [
        SAT1,
        SAT2,
        SAT3,
    ]
    on_third = _stats_on(db_session, park, third, SAT3)
    assert on_third.jubilee == 250
    assert _marks(on_third) == [
        {"kind": "jubilee", "label": "250-я пробежка"},
        {"kind": "first_here", "label": "впервые у нас"},
    ]
    on_fourth = _stats_on(db_session, park, fourth, SAT4)
    assert (on_fourth.first_here, on_fourth.jubilee) == (True, None)
    assert _marks(on_fourth) == [{"kind": "first_here", "label": "впервые у нас"}]
    assert _marks(_stats_on(db_session, park, debut, SAT4)) == [{"kind": "first_here", "label": "впервые у нас"}]


def test_special_date_among_the_three_nearest_is_a_start(db_session: Session) -> None:
    """Особая дата среди трёх ближайших — тоже старт: третья суббота уходит за
    горизонт. Особая дата чужой системы горизонт не сдвигает."""
    special = date(2026, 10, 29)
    db_session.add(StartSpecialDate(start_date=special, title="Особый старт", platforms=["five_verst"]))
    db_session.add(StartSpecialDate(start_date=TODAY + timedelta(days=1), title="Старт С95", platforms=["s95"]))
    park = _place(db_session, "Кузьминки")
    on_special = _planner(db_session, "В особый день", park, special, cached=249)
    on_third_saturday = _planner(db_session, "На третью субботу", park, SAT3, cached=249)

    assert sorted(planner_guests_service.jubilee_horizon(db_session, [(park, "five_verst")], TODAY)) == [
        SAT1,
        special,
        SAT2,
    ]
    assert _stats_on(db_session, park, on_special, special).jubilee == 250
    beyond = _stats_on(db_session, park, on_third_saturday, SAT3)
    assert (beyond.jubilee, _marks(beyond)) == (None, [{"kind": "first_here", "label": "впервые у нас"}])


def test_horizon_skips_a_cancelled_start_and_one_already_held(db_session: Session) -> None:
    """Отменённый старт — не старт: пробежки не будет. Сегодняшний, чей
    протокол уже загружен, прошёл. Ни тот, ни другой в тройку не входят."""
    cancelled = _place(db_session, "Отменили")
    cancelled.is_cancelled = True
    db_session.flush()
    assert sorted(planner_guests_service.jubilee_horizon(db_session, [(cancelled, "five_verst")], TODAY)) == [
        SAT2,
        SAT3,
        SAT4,
    ]
    park = _place(db_session, "Кузьминки")
    rows = [(park, "five_verst")]
    assert sorted(planner_guests_service.jubilee_horizon(db_session, rows, SAT1)) == [SAT1, SAT2, SAT3]
    _event(db_session, park, SAT1, 10)
    assert sorted(planner_guests_service.jubilee_horizon(db_session, rows, SAT1)) == [SAT2, SAT3, SAT4]


def test_two_saturdays_without_plans_then_a_plan_is_plus_one(db_session: Session) -> None:
    """Сценарий Дмитрия 25.10.2026: на две ближайшие субботы планов нет, на
    третью — план. Пробежка станет runs + 1, а не runs + 3, как если бы
    человек бегал без пропусков: поправка — только более ранние ПЛАНЫ. Есть
    план раньше (в другом месте) — уже runs + 2."""
    park = _place(db_session, "Кузьминки")
    elsewhere = _place(db_session, "Битца")
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    gaps = _planner(db_session, "С пропусками", park, SAT3, cached=99)
    planned = _planner(db_session, "План раньше", park, SAT3, cached=98)
    _plan(db_session, planned, elsewhere, SAT2)
    planned_99 = _planner(db_session, "План раньше, 99", park, SAT3, cached=99)
    _plan(db_session, planned_99, elsewhere, SAT1)

    assert _stats_on(db_session, park, gaps, SAT3).jubilee == 100
    assert _stats_on(db_session, park, planned, SAT3).jubilee == 100
    # 99 + план на SAT1 → SAT3 — 101-я.
    assert _stats_on(db_session, park, planned_99, SAT3).jubilee is None
    assert gaps.public_slug is not None
    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), gaps.public_slug)
    assert (card["jubilees"], card["jubilees_note"]) == ([{"label": "Будет 100-я пробежка"}], None)


def test_guest_card_jubilees_only_in_the_horizon(db_session: Session) -> None:
    """Карточка участника — те же правила: юбилеи только на три ближайших
    старта, а если план сюда дальше них — строка «Юбилеи считаем на три
    ближайших старта» вместо молчаливого «юбилеев нет»."""
    park = _place(db_session, "Кузьминки")
    _history(db_session, park, _participant(db_session, _user(db_session, "Опора")), 1)
    # SAT3 — 249-я, SAT4 — 250-я, но четвёртый старт за горизонтом.
    far = _planner(db_session, "Дальний", park, SAT3, SAT4, cached=248)
    near = _planner(db_session, "Ближний", park, SAT1, SAT4, cached=249)
    debut = _planner(db_session, "Дебютант", park, SAT1)

    def card(user: User) -> dict[str, object]:
        assert user.public_slug is not None
        return planner_guests_service.organizer_guest_card(db_session, _identity(park), user.public_slug)

    far_card = card(far)
    assert (far_card["jubilees"], far_card["jubilees_note"]) == ([], HORIZON_NOTE)
    near_card = card(near)
    assert near_card["jubilees"] == [{"label": f"Будет 250-я пробежка — {day_label(SAT1)}"}]
    assert near_card["jubilees_note"] == HORIZON_NOTE
    debut_card = card(debut)
    assert (debut_card["jubilees"], debut_card["jubilees_note"]) == ([{"label": "Будет первая пробежка"}], None)
