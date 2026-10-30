"""Планировщик забегов: планы, лимиты, даты, публичность каждого плана и
приватность блока локации, «Не бегаю», план → факт, особые даты в
админке, календарь организатора, строка и календарь «Планы» в профиле."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import (
    Event,
    EventCrosslink,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationOrganizerAccess,
    Participant,
    Platform,
    PlatformLink,
    RunPlan,
    RunResult,
    StartSpecialDate,
    User,
    VolunteerResult,
)
from app.services import run_plan_service
from app.services.organizer_access_service import invalidate_organizer_locations_cache
from app.services.planned_starts_service import PlannedStart, plan_ref
from app.services.run_plan_service import (
    PlanError,
    location_plans_block,
    minor_user_ids,
    plan_audience,
    upsert_plan,
)

ADMIN_TELEGRAM_ID = 9201
# Среда; ближайшая суббота — 24.10, следующая — 31.10, прошлая — 17.10.
TODAY = date(2026, 10, 21)
SAT1 = date(2026, 10, 24)
SAT2 = date(2026, 10, 31)
PAST_SAT = date(2026, 10, 17)
HOLIDAY = date(2026, 11, 4)
# Старт без номера в начале сентября: локация жива (последний старт моложе
# ста дней), а ≈№ от него не считается — опора только у нумерованных.
RECENT_START = date(2026, 9, 5)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "app.services.start_calendar_service.moscow_today",
        "app.services.run_plan_service.moscow_today",
        "app.api.routes.planner.moscow_today",
        "app.api.routes.organizer.moscow_today",
    ):
        monkeypatch.setattr(target, lambda: TODAY)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        admin_telegram_id=ADMIN_TELEGRAM_ID,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(
    db_session: Session, fake_redis: fakeredis.FakeRedis, auth_settings: Settings
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: auth_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _login(client: TestClient, db: Session, telegram_id: int | None = None) -> User:
    telegram_id = telegram_id or int(uuid4().int % 10_000_000_000)
    request_token = client.post("/api/auth/login-request").json()["request_token"]
    confirm = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"u{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    return db.query(User).filter(User.telegram_id == telegram_id).one()


def _platform(db: Session, code: str) -> Platform:
    return db.query(Platform).filter(Platform.code == code).one()


def _location(db: Session, code: str = "five_verst", *, started: bool = True, **fields: object) -> Location:
    """Локация; started — у неё был недавний старт: без стартов локация «ещё не
    открылась» и не планируется, как у сайта."""
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=_platform(db, code).id,
        external_key=f"plan-{suffix}",
        name=fields.pop("name", f"Парк {suffix}"),
        city="Москва",
        country="Россия",
        **fields,
    )
    db.add(location)
    db.flush()
    if started:
        db.add(
            Event(
                platform_id=location.platform_id,
                location_id=location.id,
                external_event_key=f"plan-{location.external_key}-started",
                event_date=RECENT_START,
            )
        )
        db.flush()
    return location


def _key(location: Location) -> str:
    return f"location:{location.id}"


def _user(db: Session, name: str, **fields: object) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), display_name=name, **fields)
    db.add(user)
    db.flush()
    return user


def _public_user(db: Session, name: str, **fields: object) -> User:
    """Человек, чьи публичные планы видны по имени: публичность — у каждого
    плана (_plan по умолчанию публичный), профиль открыт, если не сказано иначе."""
    return _user(db, name, **fields)


def _plan(
    db: Session,
    user: User,
    location: Location,
    on_date: date,
    created_at: datetime | None = None,
    *,
    is_public: bool = True,
) -> RunPlan:
    # created_at по умолчанию — now() транзакции: у всех планов теста он один.
    plan = RunPlan(
        user_id=user.id,
        plan_date=on_date,
        location_key=_key(location),
        location_id=location.id,
        platform_code="five_verst",
        is_public=is_public,
        **({"created_at": created_at} if created_at is not None else {}),
    )
    db.add(plan)
    db.flush()
    return plan


def _event(db: Session, location: Location, on_date: date, number: int) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"plan-{location.external_key}-{on_date.isoformat()}",
        event_date=on_date,
        event_number=number,
    )
    db.add(event)
    db.flush()
    return event


def _participant(db: Session, user: User, code: str = "five_verst", *, age: str | None = None) -> Participant:
    platform = _platform(db, code)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"plan-{uuid4().hex[:10]}",
        display_name=user.display_name,
        age_category=age,
    )
    db.add(participant)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=f"https://example.test/{participant.external_user_id}",
        )
    )
    db.flush()
    return participant


def _run(db: Session, event: Event, participant: Participant, *, age: str | None = None, finish: int | None = 1500) -> None:
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"run-{uuid4()}",
            finish_time_sec=finish,
            age_category=age,
        )
    )
    db.flush()


def _volunteer(db: Session, event: Event, participant: Participant) -> None:
    db.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"vol-{uuid4()}",
            role="Секундомер",
        )
    )
    db.flush()


def _named(name: str, handle: str, *, regular: bool = False) -> dict[str, object]:
    """Человек по имени в блоке без привязок: пробежек не посчитать. Рядом с
    именем на странице локации — только число (цифры людей проверяет
    test_planner_guests.py). regular — постоянный план сюда (значок ↻)."""
    return {"name": name, "handle": handle, "runs": None, "regular": regular}


def _org_named(name: str, handle: str) -> dict[str, object]:
    """То же в оргкалендаре: там ещё «впервые у нас», юбилей и знаковый старт —
    у человека без привязок их нет."""
    return {**_named(name, handle), "first_here": False, "jubilee": None, "marks": []}


def _save(client: TestClient, location: Location, on_date: date, **extra: object) -> object:
    return client.post(
        "/api/planner/plans",
        json={"key": _key(location), "plan_date": on_date.isoformat(), **extra},
    )


# ---------------------------------------------------------------- запись и лимиты


def test_save_plan_and_edit_it_without_hitting_the_limit(client: TestClient, db_session: Session) -> None:
    user = _login(client, db_session)
    park = _location(db_session, name="Кузьминки")
    db_session.commit()

    # Прежнее поле note молча отбрасывается — заметок нет (решение 27.10.2026).
    response = _save(client, park, SAT1, note="  взять гель  ")
    assert response.status_code == 200, response.text
    body = response.json()
    plan = body["plan"]
    assert plan["kind"] == "plan" and "note" not in plan
    # План — всегда «пробежать»: намерения в ответе нет.
    assert "intent" not in plan
    assert plan["location"]["name"] == "Кузьминки" and plan["location"]["slug"] == park.external_key
    assert plan["start"]["start_time"] == "09:00" and plan["past"] is False
    # Профиль открыт — «Публично» по умолчанию; отдельного согласия нет.
    assert plan["is_public"] is True and "needs_consent" not in body

    # Правка того же плана — это не второй план на дату.
    again = _save(client, park, SAT1)
    assert again.status_code == 200, again.text
    assert again.json()["plan"]["id"] == plan["id"]
    assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id).count() == 1


def test_regular_date_allows_one_plan(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    first = _location(db_session, name="Кузьминки")
    second = _location(db_session, name="Царицыно")
    db_session.commit()

    assert _save(client, first, SAT1).status_code == 200
    response = _save(client, second, SAT1)
    assert response.status_code == 409
    assert response.json()["detail"] == "На сб 24 октября у вас уже запланирован старт: Кузьминки"
    # На другую субботу — можно.
    assert _save(client, second, SAT2).status_code == 200


def test_special_date_allows_three_plans(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["five_verst"]))
    parks = [_location(db_session, name=f"Парк {index}") for index in range(4)]
    db_session.commit()

    for park in parks[:3]:
        response = _save(client, park, HOLIDAY)
        assert response.status_code == 200, response.text
        assert response.json()["plan"]["start"]["kind"] == "special"
        assert response.json()["plan"]["start"]["title"] == "День народного единства"
    assert _save(client, parks[3], HOLIDAY).status_code == 409


def test_full_day_plan_is_replaced_in_one_go(client: TestClient, db_session: Session) -> None:
    """День заполнен, человек выбрал другую локацию: replace_key убирает план
    этого дня в той же транзакции (решение Дмитрия 27.10.2026 — окно
    предупреждает «План в Кузьминках будет отменён», а не закрывает дату).
    Без replace_key — по-прежнему 409; плана на replace_key уже нет — тоже."""
    user = _login(client, db_session)
    first = _location(db_session, name="Кузьминки")
    second = _location(db_session, name="Царицыно")
    third = _location(db_session, name="Коломенское")
    db_session.commit()
    assert _save(client, first, SAT1).status_code == 200
    assert _save(client, second, SAT1).status_code == 409

    replaced = _save(client, second, SAT1, replace_key=_key(first))
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["plan"]["location"]["name"] == "Царицыно"
    db_session.expire_all()
    rows = db_session.query(RunPlan).filter(RunPlan.user_id == user.id, RunPlan.plan_date == SAT1).all()
    assert [row.location_id for row in rows] == [second.id]

    # Заменяемого плана уже нет (убрали в другой вкладке) — день всё ещё
    # заполнен: обычный 409, ничего не тронуто.
    stale = _save(client, third, SAT1, replace_key=_key(first))
    assert stale.status_code == 409
    assert stale.json()["detail"] == "На сб 24 октября у вас уже запланирован старт: Царицыно"
    # Есть место — replace_key ничего не убирает.
    assert _save(client, third, SAT2, replace_key=_key(second)).status_code == 200
    db_session.expire_all()
    assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id).count() == 2


def test_special_date_replaces_the_chosen_plan(client: TestClient, db_session: Session) -> None:
    """Три плана на особую дату: замена убирает именно выбранный, остальные
    остаются, и «Не бегаю» дня план тоже снимает (как обычный «Планирую»)."""
    user = _login(client, db_session)
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["five_verst"]))
    parks = [_location(db_session, name=f"Парк {index}") for index in range(4)]
    db_session.commit()
    for park in parks[:3]:
        assert _save(client, park, HOLIDAY).status_code == 200
    response = _save(client, parks[3], HOLIDAY, replace_key=_key(parks[1]))
    assert response.status_code == 200, response.text
    db_session.expire_all()
    left = {row.location_id for row in db_session.query(RunPlan).filter(RunPlan.user_id == user.id)}
    assert left == {parks[0].id, parks[2].id, parks[3].id}


def test_overfull_day_is_not_replaced_one_by_one(client: TestClient, db_session: Session) -> None:
    """Три плана на особую дату, админ её убрал — лимит стал 1. Замена одного
    места не освобождает, а окно обещало отменить один план: внятный 409 со
    списком, ничего не тронуто, и окну виден лимит дня (ревью 28.10.2026,
    SD-1)."""
    user = _login(client, db_session)
    special = StartSpecialDate(start_date=SAT2, title="Особая суббота", platforms=["five_verst"])
    db_session.add(special)
    parks = [_location(db_session, name=name) for name in ("Кузьминки", "Бутово", "Коломенское", "Сосновка")]
    db_session.commit()
    for park in parks[:3]:
        assert _save(client, park, SAT2).status_code == 200
    db_session.delete(special)
    db_session.commit()

    elsewhere = client.get("/api/planner/location", params={"key": _key(parks[3])}).json()["elsewhere"]
    assert [(item["date"], len(item["locations"]), item["full"], item["limit"]) for item in elsewhere] == [
        (SAT2.isoformat(), 3, True, 1)
    ]
    response = _save(client, parks[3], SAT2, replace_key=_key(parks[2]))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("На сб 31 октября у вас планов больше, чем можно на этот день: ")
    assert detail.endswith(". Уберите лишние в «Моих планах»")
    assert all(park.name in detail for park in parks[:3]) and parks[3].name not in detail
    db_session.expire_all()
    left = {row.location_id for row in db_session.query(RunPlan).filter(RunPlan.user_id == user.id)}
    assert left == {park.id for park in parks[:3]}


def test_plan_from_rest_day_drops_rest_even_when_replacing(client: TestClient, db_session: Session) -> None:
    """«Планирую пробежать» из дня с «Не бегаю» (правка 27.10.2026): окно от
    даты сохраняет план — отметка уходит сама, «Отменить» не нужен."""
    user = _login(client, db_session)
    park = _location(db_session, name="Зеленоград")
    db_session.commit()
    assert _rest(client, SAT1, is_public=True).status_code == 200
    assert _day_rows(db_session, user) == [(SAT1, True)]
    saved = _save(client, park, SAT1)
    assert saved.status_code == 200, saved.text
    assert _day_rows(db_session, user) == []
    mine = client.get("/api/planner/my", params={"from": SAT1.isoformat(), "to": SAT1.isoformat()}).json()
    assert [(item["kind"], item["rest"]) for item in mine["items"]] == [("plan", False)]


def test_dates_only_from_location_calendar(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    park = _location(db_session)
    db_session.commit()

    wednesday = _save(client, park, date(2026, 10, 28))
    assert wednesday.status_code == 400 and wednesday.json()["detail"] == "В этот день старта нет"
    past = _save(client, park, PAST_SAT)
    assert past.status_code == 400
    beyond = _save(client, park, TODAY + timedelta(days=372))
    assert beyond.status_code == 400
    # Особая дата другой системы — не старт этой локации.
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="Праздник S95", platforms=["s95"]))
    db_session.commit()
    assert _save(client, park, HOLIDAY).status_code == 400


def test_parkrun_and_sunday_runpark_are_not_plannable(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    parkrun = _location(db_session, "parkrun")
    sunday = _location(db_session, "runpark")
    for week in range(10):
        _event(db_session, sunday, PAST_SAT + timedelta(days=1) - timedelta(weeks=week), week + 1)
    db_session.commit()

    for location in (parkrun, sunday):
        response = _save(client, location, SAT1)
        assert response.status_code == 400
        assert response.json()["detail"] == "Эту локацию пока нельзя запланировать"
    block = client.get("/api/planner/location", params={"key": _key(sunday)}).json()
    assert block["plannable"] is False and block["reason"]
    dates = client.get("/api/planner/location/dates", params={"key": _key(parkrun)}).json()
    assert dates["plannable"] is False and dates["dates"] == []


def test_location_key_is_stored_canonical(db_session: Session) -> None:
    """Ключ пришёл `location:<id>`, а у строки есть узел каталога — в плане `catalog:<id>`."""
    user = _user(db_session, "Иван")
    park = _location(db_session)
    catalog = LocationCatalog(canonical_name="Парк", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(
            catalog_id=catalog.id, platform_id=park.platform_id, external_key=park.external_key, location_id=park.id
        )
    )
    db_session.flush()

    plan = upsert_plan(db_session, user, identity_key=_key(park), plan_date=SAT1)
    assert plan.location_key == f"catalog:{catalog.id}"
    # Тот же план находится и по старому ключу — правка, а не второй план.
    again = upsert_plan(db_session, user, identity_key=f"catalog:{catalog.id}", plan_date=SAT1)
    assert again.id == plan.id


def test_delete_plan_only_own(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    stranger = _user(db_session, "Чужой")
    park = _location(db_session)
    foreign = _plan(db_session, stranger, park, SAT1)
    db_session.commit()

    assert client.delete(f"/api/planner/plans/{foreign.id}").status_code == 404
    own = _save(client, park, SAT1).json()["plan"]["id"]
    assert client.delete(f"/api/planner/plans/{own}").status_code == 204
    assert client.delete(f"/api/planner/plans/{own}").status_code == 404


def test_planner_requires_login(client: TestClient) -> None:
    assert client.get("/api/planner/next").status_code == 401
    assert client.get("/api/planner/my", params={"from": "2026-10-01", "to": "2026-10-31"}).status_code == 401
    assert client.post("/api/planner/plans", json={"key": "x", "plan_date": "2026-10-24"}).status_code == 401
    assert client.get("/api/settings/planner").status_code == 401


def test_my_plans_range_is_limited(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    assert client.get("/api/planner/my", params={"from": "2026-10-01", "to": "2027-03-01"}).status_code == 400
    assert client.get("/api/planner/my", params={"from": "2026-10-31", "to": "2026-10-01"}).status_code == 400
    # Ширина в норме, а окно — у края дат или дальше года: 400, а не 500
    # переполнения в календаре постоянного плана (ревью 28.10.2026, FUZZ-2).
    for date_from, date_to in (("9999-09-02", "9999-12-25"), ("9999-12-26", "9999-12-31"), ("2024-01-01", "2024-02-01")):
        response = client.get("/api/planner/my", params={"from": date_from, "to": date_to})
        assert response.status_code == 400, (date_from, response.text)
    # Год назад и год вперёд с сеткой месяца — можно.
    assert client.get("/api/planner/my", params={"from": "2025-09-29", "to": "2025-11-02"}).status_code == 200
    assert client.get("/api/planner/my", params={"from": "2027-10-25", "to": "2027-11-28"}).status_code == 200


# ---------------------------------------------------------------- план → факт


def test_past_plans_show_fact(client: TestClient, db_session: Session) -> None:
    user = _login(client, db_session)
    participant = _participant(db_session, user)
    ran_park, missed_park, unknown_park, vol_park = (_location(db_session) for _ in range(4))
    weeks = [PAST_SAT - timedelta(weeks=index) for index in range(4)]
    ran_event = _event(db_session, ran_park, weeks[0], 214)
    _run(db_session, ran_event, participant, finish=1501)
    _event(db_session, missed_park, weeks[1], 77)
    vol_event = _event(db_session, vol_park, weeks[3], 12)
    _volunteer(db_session, vol_event, participant)
    _plan(db_session, user, ran_park, weeks[0])
    _plan(db_session, user, missed_park, weeks[1])
    _plan(db_session, user, unknown_park, weeks[2])
    # Планировал пробежать, а вышел волонтёром — на старте был, это не «не получилось».
    _plan(db_session, user, vol_park, weeks[3])
    _plan(db_session, user, ran_park, SAT1)
    db_session.commit()

    items = client.get("/api/planner/my", params={"from": "2026-09-01", "to": "2026-10-31"}).json()["items"]
    facts = {item["plan_date"]: item["fact"] for item in items}
    assert facts[weeks[0].isoformat()] == {
        "status": "ran",
        "finish_time_sec": 1501,
        "event_number": 214,
        "location": None,
    }
    assert facts[weeks[1].isoformat()] == {
        "status": "missed",
        "finish_time_sec": None,
        "event_number": 77,
        "location": None,
    }
    assert facts[weeks[2].isoformat()]["status"] == "unknown"
    assert facts[weeks[3].isoformat()]["status"] == "volunteered"
    # Будущий план факта не имеет.
    assert facts[SAT1.isoformat()] is None
    assert [item["past"] for item in items] == [True, True, True, True, False]

    nearest = client.get("/api/planner/next").json()["plan"]
    assert nearest["plan_date"] == SAT1.isoformat() and nearest["location"]["key"] == _key(ran_park)


# ---------------------------------------------------------------- приватность блока локации


def _privacy_world(db: Session) -> dict[str, object]:
    park = _location(db, name="Кузьминки")
    elsewhere = _location(db, name="Другой парк")
    public = _public_user(db, "Анна Публичная", public_slug="anna-public")
    private_plan = _user(db, "Борис Приватный")
    # Публичность — у каждого плана: соседний публичный план имени здесь не даёт.
    private_here = _user(db, "Вера Через-неделю")
    _plan(db, private_here, elsewhere, SAT2)
    closed_profile = _public_user(db, "Глеб Закрытый", profile_private=True)
    # Дети: публичный план — числом «1 ребёнок»; приватный план или закрытый
    # профиль — в общем числе приватных, их возраст не раскрываем.
    child = _user(db, "Даша Юная")
    child_private = _user(db, "Егор Маленький")
    child_closed = _public_user(db, "Жанна Скрытная", profile_private=True)
    old_event = _event(db, _location(db), PAST_SAT, 5)
    _run(db, old_event, _participant(db, child), age="Ж10-14 (3)")
    _run(db, old_event, _participant(db, child_private), age="М10")
    _run(db, old_event, _participant(db, child_closed), age="Ж10-14 (3)")
    _plan(db, public, park, SAT1)
    for user in (closed_profile, child, child_closed):
        _plan(db, user, park, SAT1)
    for user in (private_plan, private_here, child_private):
        _plan(db, user, park, SAT1, is_public=False)
    db.commit()
    return {"park": park, "public": public, "child": child}


def _assert_block_hides_private(payload: dict[str, object], raw: str) -> None:
    [day] = payload["dates"]  # type: ignore[index]
    assert day["date"] == SAT1.isoformat()
    assert day["total"] == 7
    # Один список планирующих пробежать — без групп и намерений.
    assert day["people"] == [_named("Анна Публичная", "anna-public")]
    assert (day["private"], day["children"]) == (5, 1)
    for key in ("groups", "hidden", "run", "volunteer", "intent"):
        assert key not in day
    for name in ("Борис", "Вера", "Глеб", "Даша", "Егор", "Жанна"):
        assert name not in raw


def test_block_hides_private_people_from_guest(client: TestClient, db_session: Session) -> None:
    world = _privacy_world(db_session)
    response = client.get("/api/planner/location", params={"key": _key(world["park"])})  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["mine"] == [] and payload["plannable"] is True
    _assert_block_hides_private(payload, response.text)


def test_block_hides_private_people_from_logged_in_and_organizer(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    world = _privacy_world(db_session)
    park = world["park"]
    viewer = _login(client, db_session)
    response = client.get("/api/planner/location", params={"key": _key(park)})  # type: ignore[arg-type]
    _assert_block_hides_private(response.json(), response.text)

    # Организатор этой локации видит то же самое — без приватных имён и заметок.
    db_session.add(LocationOrganizerAccess(user_id=viewer.id, location_key=_key(park)))  # type: ignore[arg-type]
    db_session.commit()
    invalidate_organizer_locations_cache(viewer.id)
    response = client.get("/api/planner/location", params={"key": _key(park)})  # type: ignore[arg-type]
    _assert_block_hides_private(response.json(), response.text)


def test_block_shows_owner_own_plan_in_mine(client: TestClient, db_session: Session) -> None:
    world = _privacy_world(db_session)
    owner = _login(client, db_session)
    mine = _plan(db_session, owner, world["park"], SAT1)  # type: ignore[arg-type]
    db_session.commit()
    response = client.get("/api/planner/location", params={"key": _key(world["park"])})  # type: ignore[arg-type]
    payload = response.json()
    assert [item["id"] for item in payload["mine"]] == [str(mine.id)]
    assert payload["dates"][0]["total"] == 8


def _shown(db: Session, pairs: list[tuple[User, RunPlan]]) -> dict[UUID, str]:
    """user_id → как его план виден чужим (plan_audience)."""
    audience = plan_audience(db, [(PlannedStart.of_plan(plan), user) for user, plan in pairs])
    return {user.id: audience[plan_ref(plan.id)] for user, plan in pairs}


def test_child_and_closed_profile_are_private(db_session: Session) -> None:
    adult = _public_user(db_session, "Взрослый")
    child = _public_user(db_session, "Ребёнок")
    runpark_teen = _public_user(db_session, "Подросток")
    parkrun_kid = _public_user(db_session, "Парки")
    closed = _public_user(db_session, "Закрытый", profile_private=True)
    event = _event(db_session, _location(db_session), PAST_SAT, 3)
    older = _event(db_session, _location(db_session), PAST_SAT - timedelta(weeks=60), 1)
    # Последняя по дате категория решает: ребёнок вырос — уже взрослый.
    adult_participant = _participant(db_session, adult)
    _run(db_session, older, adult_participant, age="М10-14 (1)")
    _run(db_session, event, adult_participant, age="М15-19 (2)")
    _run(db_session, event, _participant(db_session, child), age="М10")
    runpark_event = _event(db_session, _location(db_session, "runpark"), PAST_SAT, 9)
    _run(db_session, runpark_event, _participant(db_session, runpark_teen, "runpark"), age="JW11-14")
    _participant(db_session, parkrun_kid, "parkrun", age="JM10")

    assert minor_user_ids(db_session, [adult.id, child.id, runpark_teen.id, parkrun_kid.id]) == {
        child.id,
        runpark_teen.id,
        parkrun_kid.id,
    }
    park = _location(db_session)
    people = [adult, child, runpark_teen, parkrun_kid, closed]
    assert _shown(db_session, [(user, _plan(db_session, user, park, SAT1)) for user in people]) == {
        adult.id: "name",
        child.id: "child",
        runpark_teen.id: "child",
        parkrun_kid.id: "child",
        closed.id: "private",
    }


def test_upcoming_location_takes_plans_and_points_to_official_site(db_session: Session) -> None:
    """«Скоро открытие»: план на открытие ставится, блок отдаёт признак и
    страницу локации на сайте системы — окно просит сверить дату там."""
    soon = _location(
        db_session, started=False, is_upcoming=True, source_url="https://5verst.ru/gorodvlesu/"
    )
    user = _public_user(db_session, "Анна")
    _plan(db_session, user, soon, SAT1)
    block = location_plans_block(db_session, _key(soon), None)
    assert block["plannable"] is True
    assert block["upcoming"] is True and block["official_url"] == "https://5verst.ru/gorodvlesu/"
    assert [person["name"] for person in block["dates"][0]["people"]] == ["Анна"]

    regular = location_plans_block(db_session, _key(_location(db_session)), None)
    assert regular["upcoming"] is False and regular["official_url"] is None


def test_closing_profile_hides_plans_immediately(db_session: Session) -> None:
    park = _location(db_session)
    user = _public_user(db_session, "Анна")
    _plan(db_session, user, park, SAT1)
    day = location_plans_block(db_session, _key(park), None)["dates"][0]
    assert [person["name"] for person in day["people"]] == ["Анна"]
    user.profile_private = True
    db_session.flush()
    day = location_plans_block(db_session, _key(park), None)["dates"][0]
    assert (day["people"], day["private"], day["children"]) == ([], 1, 0)


def test_block_is_one_list_in_order_people_marked(db_session: Session) -> None:
    """У даты — один список планирующих пробежать, без групп и намерений:
    имена в порядке, в каком люди отмечались, приватные и дети — числами."""
    park = _location(db_session)
    first = _public_user(db_session, "Первая", public_slug="run-first")
    second = _public_user(db_session, "Второй", public_slug="run-second")
    runner = _public_user(db_session, "Бегун", public_slug="runner-late")
    noted = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    _plan(db_session, second, park, SAT1, created_at=noted + timedelta(minutes=5))
    _plan(db_session, first, park, SAT1, created_at=noted)
    _plan(db_session, _user(db_session, "Тихий"), park, SAT1, created_at=noted + timedelta(minutes=1), is_public=False)
    _plan(db_session, runner, park, SAT2)
    db_session.flush()

    first_day, second_day = location_plans_block(db_session, _key(park), None)["dates"]
    assert first_day == {
        "date": SAT1,
        "kind": "regular",
        "title": None,
        "start_time": "09:00",
        "number_forecast": None,
        "total": 3,
        "people": [
            _named("Первая", "run-first"),
            _named("Второй", "run-second"),
        ],
        "private": 1,
        "children": 0,
    }
    assert (second_day["total"], second_day["people"]) == (1, [_named("Бегун", "runner-late")])


def test_intent_in_request_is_ignored(client: TestClient, db_session: Session) -> None:
    """Прежнее поле `intent` в запросе молча отбрасывается, как любое лишнее
    поле схем проекта: план всё равно «пробежать», и в базе намерения нет."""
    user = _login(client, db_session)
    park = _location(db_session)
    db_session.commit()

    response = _save(client, park, SAT1, intent="volunteer")
    assert response.status_code == 200, response.text
    assert "intent" not in response.json()["plan"]
    assert not hasattr(RunPlan, "intent")
    assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id).count() == 1
    block = client.get("/api/planner/location", params={"key": _key(park)}).json()
    [day] = block["dates"]
    assert day["total"] == 1 and "groups" not in day and "intent" not in block["mine"][0]


def test_people_order_is_stable_on_equal_created_at(db_session: Session) -> None:
    """Ничья по времени отметки (сид, объединение профилей) решается id плана:
    порядок имён не прыгает между перезагрузками блока и оргкалендаря."""
    park = _location(db_session)
    same_moment = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    plans = [
        _plan(db_session, _public_user(db_session, f"Бегун {index}", public_slug=f"tie-{index}"), park, SAT1,
              created_at=same_moment)
        for index in range(6)
    ]
    expected = [f"tie-{plans.index(plan)}" for plan in sorted(plans, key=lambda plan: str(plan.id))]

    for _attempt in range(3):
        db_session.expire_all()
        day = location_plans_block(db_session, _key(park), None)["dates"][0]
        assert [person["handle"] for person in day["people"]] == expected


def test_child_with_public_plan_is_counted(db_session: Session) -> None:
    """Ребёнок с публичным планом и открытым профилем — «1 ребёнок»; с
    приватным — в приватных. Имени ребёнка в ответе нет ни там, ни там."""
    park = _location(db_session)
    kid = _user(db_session, "Тимофей Маленький", public_slug="timofey-kid")
    _run(db_session, _event(db_session, _location(db_session), PAST_SAT, 4), _participant(db_session, kid), age="М10")
    plan = _plan(db_session, kid, park, SAT1)

    block = location_plans_block(db_session, _key(park), None)
    day = block["dates"][0]
    assert (day["total"], day["people"], day["children"], day["private"]) == (1, [], 1, 0)
    assert "Тимофей" not in repr(block) and "timofey-kid" not in repr(block)

    plan.is_public = False
    db_session.flush()
    block = location_plans_block(db_session, _key(park), None)
    day = block["dates"][0]
    assert (day["total"], day["people"], day["children"], day["private"]) == (1, [], 0, 1)
    assert "Тимофей" not in repr(block)


# ---------------------------------------------------------------- публичность: умолчание и правка (27.10.2026)


def test_planner_settings_default_and_forced(client: TestClient, db_session: Session) -> None:
    """Окну плана — только умолчание переключателя (как профиль) и почему имя
    не видно при любом выборе. Отдельного согласия и общей настройки нет."""
    user = _login(client, db_session)
    assert client.get("/api/settings/planner").json() == {"default_public": True, "forced": None}
    assert client.put("/api/settings/planner", json={"public": True, "consent": True}).status_code == 405

    user.profile_private = True
    db_session.commit()
    assert client.get("/api/settings/planner").json() == {"default_public": False, "forced": "profile_private"}

    user.profile_private = False
    event = _event(db_session, _location(db_session), PAST_SAT, 1)
    _run(db_session, event, _participant(db_session, user), age="М10-14")
    db_session.commit()
    assert client.get("/api/settings/planner").json() == {"default_public": True, "forced": "minor"}


def test_plan_publicity_is_chosen_per_plan(client: TestClient, db_session: Session) -> None:
    """Эту субботу публично, следующую приватно: выбор — у каждого плана, его
    правка — тем же «Планирую»; без поля выбор не меняется."""
    user = _login(client, db_session)
    user.public_slug = "per-plan"
    park = _location(db_session, name="Кузьминки")
    db_session.commit()

    first = _save(client, park, SAT1)
    second = _save(client, park, SAT2, is_public=False)
    assert (first.json()["plan"]["is_public"], second.json()["plan"]["is_public"]) == (True, False)
    dates = {day["date"]: day for day in client.get("/api/planner/location", params={"key": _key(park)}).json()["dates"]}
    assert [person["handle"] for person in dates[SAT1.isoformat()]["people"]] == ["per-plan"]
    assert (dates[SAT2.isoformat()]["people"], dates[SAT2.isoformat()]["private"]) == ([], 1)

    # Правка без поля — выбор тот же; с полем — новый.
    assert _save(client, park, SAT2).json()["plan"]["is_public"] is False
    assert _save(client, park, SAT2, is_public=True).json()["plan"]["is_public"] is True
    assert _save(client, park, SAT1, is_public=False).json()["plan"]["is_public"] is False
    stored = {plan.plan_date: plan.is_public for plan in db_session.query(RunPlan).filter(RunPlan.user_id == user.id)}
    assert stored == {SAT1: False, SAT2: True}
    mine = client.get("/api/planner/my", params={"from": "2026-10-21", "to": "2026-11-30"}).json()["items"]
    assert [(item["plan_date"], item["is_public"]) for item in mine] == [
        (SAT1.isoformat(), False),
        (SAT2.isoformat(), True),
    ]


def test_closed_profile_saves_private_plans(client: TestClient, db_session: Session) -> None:
    """Закрытый профиль публичного плана не даёт: умолчание — «Приватно», и
    is_public=true в обход выключенного переключателя тоже сохраняется
    приватным. Открыл профиль — умолчание снова «Публично»."""
    user = _login(client, db_session)
    user.profile_private = True
    park = _location(db_session)
    db_session.commit()

    assert _save(client, park, SAT1).json()["plan"]["is_public"] is False
    assert _save(client, park, SAT1, is_public=True).json()["plan"]["is_public"] is False
    assert _rest(client, SAT2, is_public=True).json()["plan"]["is_public"] is False

    user.profile_private = False
    db_session.commit()
    assert _save(client, park, date(2026, 11, 7)).json()["plan"]["is_public"] is True


def test_child_chooses_publicity_but_shows_only_as_a_number(client: TestClient, db_session: Session) -> None:
    """Ребёнку переключатель доступен: публичный план — «1 ребёнок» без имени,
    приватный — в приватных."""
    user = _login(client, db_session)
    _run(db_session, _event(db_session, _location(db_session), PAST_SAT, 1), _participant(db_session, user), age="Ж10")
    park = _location(db_session)
    db_session.commit()

    saved = _save(client, park, SAT1)
    assert saved.status_code == 200 and saved.json()["plan"]["is_public"] is True
    day = location_plans_block(db_session, _key(park), None)["dates"][0]
    assert (day["people"], day["children"], day["private"]) == ([], 1, 0)

    assert _save(client, park, SAT1, is_public=False).json()["plan"]["is_public"] is False
    db_session.expire_all()
    day = location_plans_block(db_session, _key(park), None)["dates"][0]
    assert (day["people"], day["children"], day["private"]) == ([], 0, 1)


# ---------------------------------------------------------------- админка особых дат


def test_admin_special_dates_crud(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    assert client.get("/api/admin/start-dates").status_code == 403
    client.cookies.clear()
    _login(client, db_session, ADMIN_TELEGRAM_ID)

    listing = client.get("/api/admin/start-dates").json()["items"]
    assert any(item["start_date"] == "2027-01-01" for item in listing)

    created = client.post(
        "/api/admin/start-dates",
        json={"start_date": HOLIDAY.isoformat(), "title": " День народного единства ", "platforms": ["s95", "five_verst"]},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["title"] == "День народного единства" and item["platforms"] == ["five_verst", "s95"]

    duplicate = client.post(
        "/api/admin/start-dates", json={"start_date": HOLIDAY.isoformat(), "title": "Дубль", "platforms": ["s95"]}
    )
    assert duplicate.status_code == 409
    empty = client.post("/api/admin/start-dates", json={"start_date": "2026-12-31", "title": " ", "platforms": ["s95"]})
    assert empty.status_code == 400
    unknown = client.post(
        "/api/admin/start-dates", json={"start_date": "2026-12-31", "title": "X", "platforms": ["parkrun"]}
    )
    assert unknown.status_code == 400
    no_platforms = client.post("/api/admin/start-dates", json={"start_date": "2026-12-31", "title": "X", "platforms": []})
    assert no_platforms.status_code == 400

    updated = client.put(
        f"/api/admin/start-dates/{item['id']}",
        json={"start_date": "2026-11-05", "title": "Перенос", "platforms": ["runpark"]},
    )
    assert updated.status_code == 200 and updated.json()["start_date"] == "2026-11-05"
    clash = client.put(
        f"/api/admin/start-dates/{item['id']}", json={"start_date": "2027-01-01", "title": "Х", "platforms": ["s95"]}
    )
    assert clash.status_code == 409

    assert client.delete(f"/api/admin/start-dates/{item['id']}").status_code == 204
    assert client.delete(f"/api/admin/start-dates/{item['id']}").status_code == 404
    assert client.put(
        f"/api/admin/start-dates/{uuid4()}", json={"start_date": "2026-11-05", "title": "Х", "platforms": ["s95"]}
    ).status_code == 404


# ---------------------------------------------------------------- календарь организатора


def test_organizer_calendar_access_and_counts(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    park = _location(db_session, name="Кузьминки")
    _event(db_session, park, PAST_SAT - timedelta(weeks=1), 213)
    past_event = _event(db_session, park, PAST_SAT, 214)
    came = _public_user(db_session, "Пришёл")
    skipped = _public_user(db_session, "Не пришёл")
    helper = _public_user(db_session, "Вышел волонтёром")
    _run(db_session, past_event, _participant(db_session, came))
    # Планировал пробежать, а вышел волонтёром — на старте был, «пришёл».
    _volunteer(db_session, past_event, _participant(db_session, helper))
    for user in (came, skipped, helper):
        _plan(db_session, user, park, PAST_SAT)
    public = _public_user(db_session, "Анна Публичная", public_slug="anna-cal")
    hidden = _user(db_session, "Скрытый")
    kid = _public_user(db_session, "Кира Юная", public_slug="kira-kid")
    _run(db_session, _event(db_session, _location(db_session), PAST_SAT, 3), _participant(db_session, kid), age="Ж10")
    _plan(db_session, public, park, SAT2)
    _plan(db_session, hidden, park, SAT2, is_public=False)
    _plan(db_session, kid, park, SAT2)
    db_session.commit()

    viewer = _login(client, db_session)
    assert client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2026-10"}).status_code == 403

    db_session.add(LocationOrganizerAccess(user_id=viewer.id, location_key=_key(park)))
    db_session.commit()
    invalidate_organizer_locations_cache(viewer.id)
    response = client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2026-10"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["month"] == "2026-10" and payload["location"]["name"] == "Кузьминки"
    days = {day["date"]: day for day in payload["days"]}
    past = days[PAST_SAT.isoformat()]
    assert past["past"] is True and past["number"] == 214
    # Прошедший день — явка без имён.
    assert (past["planned"], past["came"]) == (3, 2)
    assert (past["people"], past["private"], past["children"]) == ([], 0, 0)
    # Неделей раньше старт был, а планов не было: пришли 0 из 0.
    assert days[(PAST_SAT - timedelta(weeks=1)).isoformat()]["came"] == 0
    future = days[SAT2.isoformat()]
    assert future["past"] is False and future["came"] is None
    assert future["planned"] == 3
    # Организатор видит то же, что посетитель страницы локации: ребёнок —
    # числом, приватный — числом.
    assert future["people"] == [_org_named("Анна Публичная", "anna-cal")]
    assert (future["private"], future["children"]) == (1, 1)
    for key in ("groups", "hidden", "run", "volunteer", "intent"):
        assert key not in future
    empty = days[SAT1.isoformat()]
    assert (empty["planned"], empty["people"], empty["private"], empty["children"]) == (0, [], 0, 0)
    for leaked in ("Скрытый", "Кира", "kira-kid"):
        assert leaked not in response.text

    assert client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2028-01"}).status_code == 400
    assert client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2026-13"}).status_code == 400
    assert client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "9999-12"}).status_code == 400


def test_service_rejects_month_out_of_range(db_session: Session) -> None:
    from types import SimpleNamespace

    park = _location(db_session)
    identity = SimpleNamespace(
        locations=[(park, "five_verst")], identity_key=_key(park), slug=park.external_key, name=park.name
    )
    with pytest.raises(PlanError):
        run_plan_service.organizer_calendar(db_session, identity, date(2024, 1, 1))


def test_parkrun_twin_merge_drops_duplicate_plans(db_session: Session) -> None:
    """Слияние двойников parkrun переписывает ключ `location:` у планов; план на
    двойника в ту же дату, что и на основную, — дубль и уходит, а не роняет
    сверку на уникальности."""
    from app.parkrun.location_keys import merge_parkrun_locations

    keep = _location(db_session, "parkrun", started=False)
    drop = _location(db_session, "parkrun", started=False)
    user = _user(db_session, "Иван")
    kept = _plan(db_session, user, keep, SAT1)
    _plan(db_session, user, drop, SAT1)
    moved = _plan(db_session, user, drop, SAT2)
    keep_id, kept_id, moved_id = keep.id, kept.id, moved.id

    merge_parkrun_locations(db_session, keep, drop)
    db_session.expire_all()

    rows = db_session.query(RunPlan).filter(RunPlan.user_id == user.id).order_by(RunPlan.plan_date).all()
    assert [(row.id, row.plan_date, row.location_key, row.location_id) for row in rows] == [
        (kept_id, SAT1, f"location:{keep_id}", keep_id),
        (moved_id, SAT2, f"location:{keep_id}", keep_id),
    ]


# ---------------------------------------------------------------- лимит дня: правка и порядок (BE-3, BE-4)


def test_editing_plan_is_not_blocked_by_the_day_limit(client: TestClient, db_session: Session) -> None:
    """Три плана на особую субботу, особую дату убрали — суббота теперь обычная,
    но поправить свой план всё равно можно: правка число планов не меняет."""
    _login(client, db_session)
    special = StartSpecialDate(start_date=SAT2, title="Особая суббота", platforms=["five_verst"])
    db_session.add(special)
    parks = [_location(db_session, name=f"Парк {index}") for index in range(4)]
    db_session.commit()
    for park in parks[:3]:
        assert _save(client, park, SAT2).status_code == 200
    db_session.delete(special)
    db_session.commit()

    edited = _save(client, parks[0], SAT2, is_public=False)
    assert edited.status_code == 200, edited.text
    assert edited.json()["plan"]["is_public"] is False
    assert db_session.query(RunPlan).filter(RunPlan.plan_date == SAT2).count() == 3
    # Новый план на эту, теперь обычную, субботу — по-прежнему нет.
    assert _save(client, parks[3], SAT2).status_code == 409


def test_day_limit_depends_on_the_date_not_on_click_order(db_session: Session) -> None:
    """Особая суббота только для 5 вёрст: у S95 это обычная суббота. Лимит —
    по самой дате, и три плана сохраняются в любом порядке нажатий."""
    db_session.add(StartSpecialDate(start_date=SAT2, title="Только 5 вёрст", platforms=["five_verst"]))
    five = [_location(db_session) for _ in range(2)]
    s95 = _location(db_session, "s95")
    db_session.flush()
    for order in ([s95, *five], [*five, s95]):
        user = _user(db_session, "Порядок")
        for park in order:
            upsert_plan(db_session, user, identity_key=_key(park), plan_date=SAT2)
        assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id).count() == 3


# ---------------------------------------------------------------- ключ в одной записи (BE-5, SEC-4)


def test_identity_key_is_stored_in_one_spelling(client: TestClient, db_session: Session) -> None:
    """«catalog:{UUID заглавными без дефисов}» UUID() принимает, но строкой
    ключ ни с чем не совпал бы: сторож каталога плана бы не увидел."""
    from app.services.location_catalog_service import catalog_key_reference_counts

    _login(client, db_session)
    park = _location(db_session)
    catalog = LocationCatalog(canonical_name="Парк", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(
            catalog_id=catalog.id, platform_id=park.platform_id, external_key=park.external_key, location_id=park.id
        )
    )
    db_session.commit()

    odd = f"catalog:{catalog.id.hex.upper()}"
    response = client.post("/api/planner/plans", json={"key": odd, "plan_date": SAT1.isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["location"]["key"] == f"catalog:{catalog.id}"
    stored = db_session.query(RunPlan).one()
    assert stored.location_key == f"catalog:{catalog.id}"
    assert catalog_key_reference_counts(db_session, [catalog.id]).get("run_plans.location_key") == 1
    # Тот же план в другой записи ключа — правка, а не второй план.
    again = client.post(
        "/api/planner/plans", json={"key": f"catalog:{{{catalog.id}}}", "plan_date": SAT1.isoformat()}
    )
    assert again.status_code == 200 and again.json()["plan"]["id"] == str(stored.id)

    block = client.get("/api/planner/location", params={"key": odd}).json()
    assert block["identity_key"] == f"catalog:{catalog.id}" and len(block["mine"]) == 1
    dates = client.get("/api/planner/location/dates", params={"key": odd}).json()
    assert dates["identity_key"] == f"catalog:{catalog.id}"
    # Не ключ — 404 у чтения и 400 у записи.
    assert client.get("/api/planner/location", params={"key": "catalog:nope"}).status_code == 404
    assert client.get("/api/planner/location/dates", params={"key": "catalog:nope"}).status_code == 404
    bad = client.post("/api/planner/plans", json={"key": "catalog:nope", "plan_date": SAT1.isoformat()})
    assert bad.status_code == 400


# ---------------------------------------------------------------- report_ro: сон постоянного закрыт (SEC-6)


def test_migration_report_ro_grants(db_session: Session) -> None:
    """DO-блок миграции 116 на роли report_ro: таблицы планов закрыты целиком,
    сообщения о планах скрыты политикой. Роль есть только на проде, и без этого теста ошибку в блоке
    увидел бы только деплой."""
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    connection = db_session.connection()
    if connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'report_ro'")).first() is not None:
        pytest.skip("report_ro уже есть в кластере — его права тест не трогает")
    if not connection.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
        pytest.skip("Нужен суперпользователь: тест создаёт роль в своей транзакции")
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "116_run_plans.py"
    spec = importlib.util.spec_from_file_location("migration_116_run_plans", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    # CREATE ROLE в Postgres транзакционный: откат фикстуры уберёт и роль.
    connection.execute(text("CREATE ROLE report_ro NOLOGIN"))
    connection.execute(text("GRANT USAGE ON SCHEMA public TO report_ro"))
    # Как шаг 3 scripts/create_report_ro_role.sql: сначала открыто всё.
    connection.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO report_ro"))
    connection.exec_driver_sql(migration._REPORT_RO_GRANTS)

    def as_report_ro(sql: str) -> str | None:
        """None — запрос прошёл, иначе текст ошибки."""
        savepoint = connection.begin_nested()
        try:
            connection.execute(text("SET LOCAL ROLE report_ro"))
            connection.execute(text(sql)).all()
        except DBAPIError as exc:
            savepoint.rollback()
            return str(exc.orig)
        savepoint.rollback()
        return None

    # Планы людей закрыты целиком, и разовые, и постоянные.
    assert "permission denied" in (as_report_ro("SELECT id FROM run_plans") or "")
    assert "permission denied" in (as_report_ro("SELECT id FROM run_plan_series") or "")

    # Сообщения о планах скрыты политикой, остальные виды роль читает.
    user = _user(db_session, "Читаемый")
    for kind in ("plan_reminders", "plan_cancellations", "plan_series", "runs"):
        connection.execute(
            text(
                "INSERT INTO notification_deliveries (user_id, kind, dedupe_key, status, channel, payload) "
                "VALUES (:u, :k, :d, 'sent', 'telegram', CAST('{}' AS jsonb))"
            ),
            {"u": user.id, "k": kind, "d": f"t-{kind}"},
        )

    def visible_kinds() -> set[str]:
        connection.execute(text("SET LOCAL ROLE report_ro"))
        try:
            rows = connection.execute(
                text("SELECT kind FROM notification_deliveries WHERE user_id = :u"), {"u": user.id}
            ).all()
        finally:
            connection.execute(text("RESET ROLE"))
        return {row[0] for row in rows}

    assert visible_kinds() == {"runs"}


# ---------------------------------------------------------------- план → факт по приоритету событий (CAL-2)


def _two_row_identity(db: Session) -> tuple[Location, Location, str]:
    """Точка S95 и её зеркало RunPark в одном узле каталога."""
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
    return main, mirror, f"catalog:{catalog.id}"


def _identity_plan(db: Session, user: User, key: str, location: Location, on_date: date) -> RunPlan:
    plan = RunPlan(
        user_id=user.id,
        plan_date=on_date,
        location_key=key,
        location_id=location.id,
        platform_code="s95",
    )
    db.add(plan)
    db.flush()
    return plan


def test_mirror_protocol_alone_does_not_mean_missed(db_session: Session) -> None:
    """Протокол зеркала пришёл, а своей системы ещё нет — это «неизвестно», а
    не серое «не получилось»."""
    main, mirror, key = _two_row_identity(db_session)
    user = _user(db_session, "Только S95")
    _participant(db_session, user, "s95")
    _event(db_session, mirror, PAST_SAT, 234)
    plan = _identity_plan(db_session, user, key, main, PAST_SAT)

    fact = run_plan_service.plan_facts(db_session, user.id, [plan], today=TODAY)[f"plan:{plan.id}"]
    assert fact == {"status": "unknown", "finish_time_sec": None, "event_number": None}

    # Протокол своей системы пришёл — теперь «не получилось», номер свой.
    _event(db_session, main, PAST_SAT, 175)
    fact = run_plan_service.plan_facts(db_session, user.id, [plan], today=TODAY)[f"plan:{plan.id}"]
    assert fact == {"status": "missed", "finish_time_sec": None, "event_number": 175}


def test_fact_number_comes_from_the_plan_system(db_session: Session) -> None:
    """Человек в обоих протоколах одного старта — номер системы плана, а не
    той, что попалась первой."""
    main, mirror, key = _two_row_identity(db_session)
    user = _user(db_session, "Обе системы")
    main_event = _event(db_session, main, PAST_SAT, 175)
    mirror_event = _event(db_session, mirror, PAST_SAT, 233)
    _run(db_session, mirror_event, _participant(db_session, user, "runpark"), finish=1600)
    _run(db_session, main_event, _participant(db_session, user, "s95"), finish=1600)
    plan = _identity_plan(db_session, user, key, main, PAST_SAT)

    for _ in range(3):
        fact = run_plan_service.plan_facts(db_session, user.id, [plan], today=TODAY)[f"plan:{plan.id}"]
        assert fact == {"status": "ran", "finish_time_sec": 1600, "event_number": 175}


def test_fact_counts_a_run_found_only_in_the_mirror(db_session: Session) -> None:
    """Привязка только к зеркалу: его событие — вторичная сторона кросслинка,
    но пробежка человека там есть — «пробежал»."""
    main, mirror, key = _two_row_identity(db_session)
    user = _user(db_session, "Только RunPark")
    main_event = _event(db_session, main, PAST_SAT, 175)
    mirror_event = _event(db_session, mirror, PAST_SAT, 233)
    db_session.add(EventCrosslink(primary_event_id=main_event.id, secondary_event_id=mirror_event.id))
    _run(db_session, mirror_event, _participant(db_session, user, "runpark"), finish=1700)
    plan = _identity_plan(db_session, user, key, main, PAST_SAT)

    fact = run_plan_service.plan_facts(db_session, user.id, [plan], today=TODAY)[f"plan:{plan.id}"]
    assert fact["status"] == "ran" and fact["finish_time_sec"] == 1700


# ---------------------------------------------------------------- календарь организатора: номер и особые даты


def _calendar(db: Session, rows: list[tuple[Location, str]], month: date) -> dict[date, dict[str, object]]:
    from types import SimpleNamespace

    main = rows[0][0]
    identity = SimpleNamespace(locations=rows, identity_key=_key(main), slug=main.external_key, name=main.name)
    payload = run_plan_service.organizer_calendar(db, identity, month)
    return {day["date"]: day for day in payload["days"]}


def test_organizer_calendar_number_is_of_the_main_system(db_session: Session) -> None:
    """Номер дня — у основной системы (первой в строках), а не у первой по
    алфавиту; день только с зеркалом — без номера (CAL-3)."""
    main, mirror, _key_ = _two_row_identity(db_session)
    linked_main = _event(db_session, main, PAST_SAT - timedelta(weeks=2), 173)
    linked_mirror = _event(db_session, mirror, PAST_SAT - timedelta(weeks=2), 232)
    db_session.add(EventCrosslink(primary_event_id=linked_main.id, secondary_event_id=linked_mirror.id))
    # Связки ещё нет: оба протокола одного старта.
    _event(db_session, main, PAST_SAT - timedelta(weeks=1), 174)
    _event(db_session, mirror, PAST_SAT - timedelta(weeks=1), 233)
    # Протокол основной системы ещё не пришёл.
    _event(db_session, mirror, PAST_SAT, 234)
    db_session.flush()

    days = _calendar(db_session, [(main, "s95"), (mirror, "runpark")], date(2026, 10, 1))
    assert days[PAST_SAT - timedelta(weeks=2)]["number"] == 173
    assert days[PAST_SAT - timedelta(weeks=1)]["number"] == 174
    assert days[PAST_SAT]["number"] is None
    assert days[PAST_SAT]["came"] == 0

    # Переезд без зеркала (Раменское: 5 вёрст → S95) номер прежней системы сохраняет.
    moved_to = _location(db_session, "s95")
    moved_from = _location(db_session, "five_verst", is_paused=True)
    _event(db_session, moved_from, PAST_SAT - timedelta(weeks=1), 190)
    _event(db_session, moved_to, PAST_SAT, 12)
    days = _calendar(db_session, [(moved_to, "s95"), (moved_from, "five_verst")], date(2026, 10, 1))
    assert days[PAST_SAT - timedelta(weeks=1)]["number"] == 190
    assert days[PAST_SAT]["number"] == 12


def test_organizer_calendar_past_special_dates(db_session: Session) -> None:
    """Прошедшая особая дата с планами, но без протокола, — с названием; особая
    дата чужой системы не подписывает перенесённый старт (BE-6)."""
    park = _location(db_session, name="Кузьминки")
    wednesday, tuesday = date(2026, 10, 14), date(2026, 10, 6)
    db_session.add(StartSpecialDate(start_date=wednesday, title="Праздник 5 вёрст", platforms=["five_verst"]))
    db_session.add(StartSpecialDate(start_date=tuesday, title="Праздник S95", platforms=["s95"]))
    _event(db_session, park, tuesday, 50)
    _plan(db_session, _public_user(db_session, "Планировал"), park, wednesday)
    db_session.flush()

    days = _calendar(db_session, [(park, "five_verst")], date(2026, 10, 1))
    assert (days[wednesday]["kind"], days[wednesday]["title"], days[wednesday]["planned"]) == (
        "special",
        "Праздник 5 вёрст",
        1,
    )
    assert (days[tuesday]["kind"], days[tuesday]["title"], days[tuesday]["number"]) == ("regular", None, 50)


def test_plans_on_a_date_gone_from_the_calendar_are_not_plans(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Админ убрал особую дату с планами: такой план не напоминается и в
    строке профиля не стоит (still_on) — значит, и на странице локации, в
    оргкалендаре, «Кто у нас бежит» и карточке участника его нет, «Обзор» не
    зовёт его ближайшим, а вкладка «Планы» профиля без других планов не
    активна (ревью 28.10.2026, SD-2…SD-4). «Не бегаю» на пятнице, которая
    больше не день старта, в календаре профиля тоже не стоит."""
    from types import SimpleNamespace

    from app.services import planner_guests_service

    monkeypatch.setattr(planner_guests_service, "moscow_today", lambda: TODAY)
    special = StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["five_verst"])
    db_session.add(special)
    park = _location(db_session, name="Кузьминки")
    runner = _public_user(db_session, "Праздничный", public_slug="holiday-runner")
    _plan(db_session, runner, park, HOLIDAY)
    resting = _public_user(db_session, "Отдыхающий", public_slug="holiday-rest")
    db_session.add(RunPlan(user_id=resting.id, plan_date=HOLIDAY, rest=True, is_public=True))
    db_session.flush()
    identity = SimpleNamespace(
        identity_key=_key(park), locations=[(park, "five_verst")], slug=park.external_key, name=park.name
    )
    assert HOLIDAY in {day["date"] for day in location_plans_block(db_session, _key(park), None)["dates"]}

    db_session.delete(special)
    db_session.flush()
    assert location_plans_block(db_session, _key(park), None)["dates"] == []
    november = run_plan_service.organizer_calendar(db_session, identity, HOLIDAY)
    assert HOLIDAY not in {day["date"] for day in november["days"]}
    assert planner_guests_service.organizer_guests(db_session, identity)["dates"] == []
    with pytest.raises(planner_guests_service.GuestNotFound):
        planner_guests_service.organizer_guest_card(db_session, identity, "holiday-runner")
    assert run_plan_service.next_plan(db_session, runner) is None
    _plan(db_session, runner, park, SAT2)
    db_session.flush()
    assert run_plan_service.next_plan(db_session, runner)["plan_date"] == SAT2
    db_session.query(RunPlan).filter(RunPlan.user_id == runner.id, RunPlan.plan_date == SAT2).delete()
    db_session.flush()
    assert run_plan_service.profile_plans_tab(db_session, runner, None) == "empty"
    assert run_plan_service.profile_plans_tab(db_session, resting, None) == "empty"
    assert run_plan_service.profile_plan_calendar(db_session, resting, None, HOLIDAY)["days"] == []


# ---------------------------------------------------------------- особые даты для всех (F1)


def test_public_special_dates(client: TestClient, db_session: Session) -> None:
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["s95", "five_verst"]))
    db_session.commit()

    response = client.get("/api/planner/special-dates", params={"from": "2026-10-01", "to": "2027-01-31"})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["date"] for item in items] == sorted(item["date"] for item in items)
    by_date = {item["date"]: item for item in items}
    assert by_date[HOLIDAY.isoformat()] == {
        "date": HOLIDAY.isoformat(),
        "title": "День народного единства",
        "platforms": ["s95", "five_verst"],
    }
    assert by_date["2027-01-01"]["title"] == "Новогодний старт"
    assert set(items[0]) == {"date", "title", "platforms"}

    narrow = client.get("/api/planner/special-dates", params={"from": "2026-11-05", "to": "2026-12-31"}).json()
    assert HOLIDAY.isoformat() not in {item["date"] for item in narrow["items"]}
    # 400 дней — можно, 401 — уже нет.
    assert client.get("/api/planner/special-dates", params={"from": "2026-01-01", "to": "2027-02-05"}).status_code == 200
    assert client.get("/api/planner/special-dates", params={"from": "2026-01-01", "to": "2027-02-06"}).status_code == 400
    assert client.get("/api/planner/special-dates", params={"from": "2026-11-01", "to": "2026-10-01"}).status_code == 400
    assert client.get("/api/planner/special-dates", params={"from": "2026-11-01"}).status_code == 422


# ---------------------------------------------------------------- строка в профиле (26–27.10.2026)


def _profile(client: TestClient, handle: str) -> object:
    return client.get("/api/planner/profile", params={"handle": handle})


def test_profile_line_is_shown_to_everyone_when_public(client: TestClient, db_session: Session) -> None:
    """Ближайший публичный план — строкой в шапке профиля, гостю тоже."""
    park = _location(db_session, name="Зеленоград")
    runner = _public_user(db_session, "Анна Публичная", public_slug="anna-profile")
    _plan(db_session, runner, park, SAT1)
    db_session.commit()

    response = _profile(client, "anna-profile")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "date": SAT1.isoformat(),
        "kind": "regular",
        "title": None,
        "places": [{"name": "Зеленоград", "slug": park.external_key, "cancelled": False, "regular": False}],
        "upcoming_count": 1,
        "only_me": False,
    }
    # По номеру профиля — то же самое.
    db_session.refresh(runner)
    assert _profile(client, str(runner.serial_id)).json()["places"][0]["name"] == "Зеленоград"
    assert _profile(client, "no-such-runner").status_code == 404


@pytest.mark.parametrize("reason", ["private", "closed_profile", "child"])
def test_profile_line_hidden_from_others_but_shown_to_owner(
    client: TestClient, db_session: Session, reason: str
) -> None:
    """Другим — по тем же правилам, что имя на странице локации; хозяину —
    всегда, с пометкой only_me."""
    owner = _login(client, db_session)
    owner.public_slug = "owner-profile"
    owner.profile_private = reason == "closed_profile"
    if reason == "child":
        event = _event(db_session, _location(db_session), PAST_SAT, 5)
        _run(db_session, event, _participant(db_session, owner), age="Ж10-14 (3)")
    park = _location(db_session, name="Зеленоград")
    _plan(db_session, owner, park, SAT1, is_public=reason != "private")
    db_session.commit()

    mine = _profile(client, "owner-profile").json()
    assert mine["only_me"] is True and [place["name"] for place in mine["places"]] == ["Зеленоград"]
    assert mine["upcoming_count"] == 0
    client.cookies.clear()
    assert _profile(client, "owner-profile").json() is None
    _login(client, db_session)
    assert _profile(client, "owner-profile").json() is None


def test_profile_line_owner_sees_public_plan_without_only_me(client: TestClient, db_session: Session) -> None:
    owner = _login(client, db_session)
    owner.public_slug = "owner-public"
    _plan(db_session, owner, _location(db_session, name="Зеленоград"), SAT1)
    db_session.commit()

    assert _profile(client, "owner-public").json()["only_me"] is False


def test_profile_line_is_the_nearest_public_plan_on_any_date(client: TestClient, db_session: Session) -> None:
    """Не только ближайшая суббота (решение Дмитрия 27.10.2026): другим —
    ближайший ПУБЛИЧНЫЙ план, приватный перед ним пропускается; хозяину —
    ближайший любой, с only_me. upcoming_count — даты публичных планов."""
    runner = _login(client, db_session)
    runner.public_slug = "anna-later"
    park, other = _location(db_session, name="Зеленоград"), _location(db_session, name="Кузьминки")
    _plan(db_session, runner, other, SAT1, is_public=False)
    _plan(db_session, runner, park, SAT2)
    _plan(db_session, runner, park, date(2026, 11, 14))
    db_session.commit()

    mine = _profile(client, "anna-later").json()
    assert (mine["date"], mine["only_me"], mine["upcoming_count"]) == (SAT1.isoformat(), True, 2)
    client.cookies.clear()
    line = _profile(client, "anna-later").json()
    assert (line["date"], line["only_me"], line["upcoming_count"]) == (SAT2.isoformat(), False, 2)
    assert [place["name"] for place in line["places"]] == ["Зеленоград"]
    assert "Кузьминки" not in repr(line)


def test_profile_line_takes_special_date_before_saturday(client: TestClient, db_session: Session) -> None:
    """План на особую дату раньше субботы — ближайший: строка о нём, с
    названием даты и до трёх локаций; у плана только на субботу — о субботе."""
    holiday = date(2026, 10, 23)  # пятница перед SAT1
    db_session.add(StartSpecialDate(start_date=holiday, title="Праздничный старт", platforms=["five_verst"]))
    parks = [_location(db_session, name=name) for name in ("Кузьминки", "Коломенское")]
    festive = _public_user(db_session, "Праздничный", public_slug="festive")
    saturday_only = _public_user(db_session, "Субботний", public_slug="saturday-only")
    marked = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    for index, park in enumerate(parks):
        # Локации строки — в порядке отметок.
        _plan(db_session, festive, park, holiday, created_at=marked + timedelta(minutes=index))
    _plan(db_session, saturday_only, parks[0], SAT1)
    db_session.commit()

    line = _profile(client, "festive").json()
    assert (line["date"], line["kind"], line["title"]) == (holiday.isoformat(), "special", "Праздничный старт")
    assert [place["name"] for place in line["places"]] == ["Кузьминки", "Коломенское"]
    assert _profile(client, "saturday-only").json()["date"] == SAT1.isoformat()


def test_nearest_start_skips_special_date_of_other_system(client: TestClient, db_session: Session) -> None:
    """Праздник S95 в пятницу бегуну 5 вёрст не ближайший старт: «Обзор»
    говорит о субботе (ревью 26.10.2026, PLAN-REV-1). Кто бегает и в S95 — у
    того пятница своя, и план на субботу её не заменяет. План на чужую особую
    дату делает её своей. Строка профиля — о ближайшем плане, а не о дне
    старта: у обоих — суббота."""
    holiday = date(2026, 10, 23)  # пятница перед SAT1
    db_session.add(StartSpecialDate(start_date=holiday, title="Праздник S95", platforms=["s95"]))
    park = _location(db_session, name="Зеленоград")
    s95_park = _location(db_session, "s95", name="Парк S95")
    five_verst_runner = _public_user(db_session, "Только 5 вёрст", public_slug="five-verst-runner")
    _plan(db_session, five_verst_runner, park, SAT1)
    both = _public_user(db_session, "Обе системы", public_slug="both-systems")
    _participant(db_session, both, "s95")
    _plan(db_session, both, park, SAT1)
    db_session.commit()

    line = _profile(client, "five-verst-runner").json()
    assert (line["date"], line["kind"]) == (SAT1.isoformat(), "regular")
    assert _profile(client, "both-systems").json()["date"] == SAT1.isoformat()

    _login(client, db_session)
    assert _save(client, park, SAT1).status_code == 200
    day = client.get("/api/planner/next").json()["day"]
    assert (day["date"], day["kind"], day["planned"]) == (SAT1.isoformat(), "regular", True)
    assert _save(client, s95_park, holiday).status_code == 200
    day = client.get("/api/planner/next").json()["day"]
    assert (day["date"], day["title"], day["planned"]) == (holiday.isoformat(), "Праздник S95", True)


def test_day_row_writes_take_user_lock(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждая запись строки дня и удаление плана — под блокировкой человека:
    решение по устаревшему чтению стирало параллельное «Не бегаю» (ревью
    26.10.2026, PLAN-REV-2)."""
    user = _user(db_session, "Иван")
    park = _location(db_session)
    plan = _plan(db_session, user, park, SAT2)
    locked: list[str] = []
    real_lock = run_plan_service._lock_user

    def spy_lock(db: Session, user_id: UUID) -> None:
        locked.append("lock")
        real_lock(db, user_id)

    monkeypatch.setattr(run_plan_service, "_lock_user", spy_lock)

    for name, action in (
        ("set_rest", lambda: run_plan_service.set_rest(db_session, user, SAT1)),
        ("clear_rest", lambda: run_plan_service.clear_rest(db_session, user, SAT1)),
        ("delete_plan", lambda: run_plan_service.delete_plan(db_session, user, plan.id)),
    ):
        locked.clear()
        action()
        assert locked == ["lock"], name


def test_profile_line_on_saturday_lasts_all_day(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В саму субботу ближайший старт — сегодняшний, до конца дня."""
    for target in ("app.services.start_calendar_service.moscow_today", "app.services.run_plan_service.moscow_today"):
        monkeypatch.setattr(target, lambda: SAT1)
    runner = _public_user(db_session, "Анна", public_slug="anna-today")
    _plan(db_session, runner, _location(db_session, name="Зеленоград"), SAT1)
    db_session.commit()

    assert _profile(client, "anna-today").json()["date"] == SAT1.isoformat()


def test_profile_line_skips_paused_location_and_marks_cancelled(client: TestClient, db_session: Session) -> None:
    paused = _location(db_session, name="На паузе")
    cancelled = _location(db_session, name="Отменили", is_cancelled=True, cancel_reason="Субботник")
    first = _public_user(db_session, "Первый", public_slug="paused-runner")
    second = _public_user(db_session, "Второй", public_slug="cancelled-runner")
    _plan(db_session, first, paused, SAT1)
    _plan(db_session, second, cancelled, SAT1)
    paused.is_paused = True
    db_session.commit()

    assert _profile(client, "paused-runner").json() is None
    assert _profile(client, "cancelled-runner").json()["places"] == [
        {"name": "Отменили", "slug": cancelled.external_key, "cancelled": True, "regular": False}
    ]


# ---------------------------------------------------------------- «Не бегаю» (26.10.2026)


def _rest(client: TestClient, on_date: date, **body: object) -> object:
    return client.put(f"/api/planner/rest/{on_date.isoformat()}", json=body or None)


def _day_rows(db: Session, user: User) -> list[tuple[date, bool]]:
    db.expire_all()
    return [
        (row.plan_date, row.rest)
        for row in db.query(RunPlan)
        .filter(RunPlan.user_id == user.id, RunPlan.location_key.is_(None))
        .order_by(RunPlan.plan_date)
    ]


def test_rest_only_on_start_days_in_horizon(client: TestClient, db_session: Session) -> None:
    _login(client, db_session)
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["s95"]))
    db_session.commit()

    saved = _rest(client, SAT1)
    assert saved.status_code == 200, saved.text
    item = saved.json()["plan"]
    assert (item["kind"], item["rest"], item["location"]) == ("day", True, None) and "note" not in item
    # Особая дата — тоже день старта, какой бы системы она ни была.
    assert _rest(client, HOLIDAY).status_code == 200
    wednesday = _rest(client, date(2026, 10, 28))
    assert wednesday.status_code == 400
    assert wednesday.json()["detail"] == "«Не бегаю» отмечается на день старта — субботу или особую дату"
    assert _rest(client, PAST_SAT).status_code == 400
    assert _rest(client, SAT1 + timedelta(weeks=60)).status_code == 400
    # Повтор — та же строка.
    assert _rest(client, SAT1).json()["plan"]["id"] == item["id"]


def test_rest_over_plans_asks_and_replaces(client: TestClient, db_session: Session) -> None:
    user = _login(client, db_session)
    park = _location(db_session, name="Зеленоград")
    db_session.commit()
    plan_id = _save(client, park, SAT1).json()["plan"]["id"]

    conflict = _rest(client, SAT1)
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["detail"] == "На сб 24 октября у вас в планах: Зеленоград. Убрать план и отметить «Не бегаю»?"
    assert [plan["id"] for plan in body["plans"]] == [plan_id]
    assert body["plans"][0]["location"]["name"] == "Зеленоград"
    # Без replace ничего не поменялось.
    assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id).count() == 1

    replaced = _rest(client, SAT1, replace=True)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["plan"]["rest"] is True
    assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id, RunPlan.location_key.isnot(None)).count() == 0
    assert _day_rows(db_session, user) == [(SAT1, True)]


def test_rest_question_lists_several_plans(client: TestClient, db_session: Session) -> None:
    user = _login(client, db_session)
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["five_verst"]))
    first, second = _location(db_session, name="Кузьминки"), _location(db_session, name="Коломенское")
    # Планы в окне — в порядке отметок.
    marked = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    _plan(db_session, user, first, HOLIDAY, created_at=marked)
    _plan(db_session, user, second, HOLIDAY, created_at=marked + timedelta(minutes=1))
    db_session.commit()

    conflict = _rest(client, HOLIDAY)
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == (
        "На ср 4 ноября у вас в планах: Кузьминки, Коломенское. Убрать планы и отметить «Не бегаю»?"
    )


def test_plan_clears_rest(client: TestClient, db_session: Session) -> None:
    """Передумал отдыхать: план пробежки снимает «Не бегаю» — строка дня
    уходит. Лимит дня отметку не считает."""
    user = _login(client, db_session)
    park = _location(db_session, name="Зеленоград")
    db_session.commit()

    assert _rest(client, SAT1).status_code == 200
    assert _save(client, park, SAT1).status_code == 200
    assert _day_rows(db_session, user) == []


def test_clear_rest_removes_the_day_row(client: TestClient, db_session: Session) -> None:
    """Заметок нет (решение 27.10.2026): строка дня — только отметка, и снятая
    отметка уносит строку (строку дня без отметки не пускает и база —
    test_rest_check_constraint)."""
    user = _login(client, db_session)
    assert _rest(client, SAT1).status_code == 200
    assert _day_rows(db_session, user) == [(SAT1, True)]
    assert client.delete(f"/api/planner/rest/{SAT1.isoformat()}").status_code == 204
    assert _day_rows(db_session, user) == []
    # Снять несуществующую отметку — не ошибка.
    assert client.delete(f"/api/planner/rest/{SAT2.isoformat()}").status_code == 204
    # Прежних ручек заметок больше нет.
    assert client.put(f"/api/planner/day-notes/{SAT1.isoformat()}", json={"note": "x"}).status_code in (404, 405)


def test_rest_stays_out_of_location_and_organizer_views(client: TestClient, db_session: Session) -> None:
    """«Не бегаю» нет ни в блоке локации, ни в оргкалендаре, ни в «Кто у нас
    бежит», ни в строке профиля, ни в напоминаниях. Чужим она видна только в
    календаре «Планы» профиля — и только публичная (test_profile_calendar_*)."""
    from app.services import plan_notification_service
    from app.services.notification_service import set_channel_enabled

    owner = _login(client, db_session)
    owner.public_slug = "rest-owner"
    owner.telegram_chat_id = owner.telegram_id
    park = _location(db_session, name="Зеленоград")
    db_session.commit()
    set_channel_enabled(db_session, owner, "telegram", True)
    db_session.commit()
    assert _save(client, park, SAT1).status_code == 200
    assert _rest(client, SAT1, replace=True).status_code == 200

    mine = client.get("/api/planner/my", params={"from": "2026-10-21", "to": "2026-10-31"}).json()["items"]
    assert [(item["kind"], item["rest"]) for item in mine] == [("day", True)]
    assert _profile(client, "rest-owner").json() is None
    block = client.get("/api/planner/location", params={"key": _key(park)}).json()
    assert block["dates"] == [] and block["mine"] == []
    db_session.add(LocationOrganizerAccess(user_id=owner.id, location_key=_key(park)))
    db_session.commit()
    invalidate_organizer_locations_cache(owner.id)
    calendar = client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2026-10"}).json()
    assert all(day["planned"] == 0 for day in calendar["days"])
    guests = client.get(f"/api/organizer/{park.external_key}/guests").json()
    assert guests["dates"] == []
    evening = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)  # пятница, 19:00 МСК
    assert plan_notification_service.send_plan_reminders(db_session, now=evening) == 0


def test_past_rest_day_shows_quiet_fact(client: TestClient, db_session: Session) -> None:
    """Прошедший «Не бегаю»: пробежал всё-таки — «ran», иначе тихо «rested»."""
    user = _login(client, db_session)
    participant = _participant(db_session, user)
    elsewhere = _location(db_session, name="Где-то")
    ran_day, rested_day = PAST_SAT, PAST_SAT - timedelta(weeks=1)
    _run(db_session, _event(db_session, elsewhere, ran_day, 41), participant, finish=1620)
    # Волонтёрство пробежкой не считается: «не бегали» — правда.
    _volunteer(db_session, _event(db_session, elsewhere, rested_day, 40), participant)
    for on_date in (ran_day, rested_day):
        db_session.add(RunPlan(user_id=user.id, plan_date=on_date, rest=True))
    db_session.commit()

    items = client.get("/api/planner/my", params={"from": "2026-10-01", "to": "2026-10-31"}).json()["items"]
    facts = {item["plan_date"]: item["fact"] for item in items}
    assert facts[ran_day.isoformat()] == {"status": "ran", "finish_time_sec": 1620, "event_number": 41, "location": None}
    assert facts[rested_day.isoformat()] == {
        "status": "rested",
        "finish_time_sec": None,
        "event_number": None,
        "location": None,
    }


def test_next_reports_nearest_start_day_state(client: TestClient, db_session: Session) -> None:
    """«Обзор»: на ближайший старт нет плана — «Планирую» и «Не бегаю»;
    стоит отметка — «не бегаете · Отменить»."""
    _login(client, db_session)
    park = _location(db_session, name="Зеленоград")
    db_session.commit()

    empty = client.get("/api/planner/next").json()
    assert empty == {
        "plan": None,
        "day": {
            "date": SAT1.isoformat(),
            "kind": "regular",
            "title": None,
            "planned": False,
            "series": False,
            "rest": False,
        },
        "series": None,
        "series_hint": None,
    }
    # План через неделю — не на ближайший старт.
    _save(client, park, SAT2)
    later = client.get("/api/planner/next").json()
    assert later["plan"]["plan_date"] == SAT2.isoformat()
    assert (later["day"]["planned"], later["day"]["rest"]) == (False, False)
    _rest(client, SAT1)
    assert client.get("/api/planner/next").json()["day"]["rest"] is True
    _save(client, park, SAT1)
    day = client.get("/api/planner/next").json()["day"]
    assert (day["planned"], day["rest"]) == (True, False)


def test_rest_check_constraint(db_session: Session) -> None:
    """База держит виды строк: у плана с локацией «Не бегаю» не бывает, строка
    дня без отметки не живёт."""
    from sqlalchemy.exc import IntegrityError

    user = _user(db_session, "Иван")
    park = _location(db_session)
    for row in (
        RunPlan(user_id=user.id, plan_date=SAT1, location_key=_key(park), location_id=park.id, rest=True),
        RunPlan(user_id=user.id, plan_date=SAT2, rest=False),
    ):
        savepoint = db_session.begin_nested()
        db_session.add(row)
        with pytest.raises(IntegrityError):
            db_session.flush()
        savepoint.rollback()



def test_rest_publicity_default_and_edit(client: TestClient, db_session: Session) -> None:
    """«Не бегаю» по умолчанию — как профиль; повтор с полем — правка
    публичности стоящей отметки, без поля — без изменений; снятая и
    поставленная снова — снова умолчание."""
    _login(client, db_session)
    assert _rest(client, SAT1).json()["plan"]["is_public"] is True
    assert _rest(client, SAT1, is_public=False).json()["plan"]["is_public"] is False
    assert _rest(client, SAT1).json()["plan"]["is_public"] is False
    assert client.delete(f"/api/planner/rest/{SAT1.isoformat()}").status_code == 204
    assert _rest(client, SAT1).json()["plan"]["is_public"] is True


# ---------------------------------------------------------------- календарь «Планы» в профиле (27.10.2026)


def _calendar_of(client: TestClient, handle: str, month: str = "2026-10") -> object:
    return client.get("/api/planner/profile/calendar", params={"handle": handle, "month": month})


def test_profile_calendar_shows_only_public_plans_and_rest(client: TestClient, db_session: Session) -> None:
    """Вкладка «Планы» профиля: публичные планы (места) и публичные «Не бегаю»
    по дням месяца; приватные — пустота. С сегодняшнего дня, без заметок,
    мимо кэшей."""
    runner = _login(client, db_session)
    runner.public_slug = "cal-runner"
    park, other = _location(db_session, name="Зеленоград"), _location(db_session, name="Кузьминки")
    # Прошедший план в календарь профиля не идёт: факт — только владельцу.
    _plan(db_session, runner, park, PAST_SAT)
    db_session.commit()
    assert _save(client, park, SAT1).status_code == 200
    assert _save(client, other, SAT2, is_public=False).status_code == 200
    nov7, nov14 = date(2026, 11, 7), date(2026, 11, 14)
    assert _rest(client, nov7).status_code == 200
    assert _rest(client, nov14, is_public=False).status_code == 200
    client.cookies.clear()

    october = _calendar_of(client, "cal-runner")
    assert october.status_code == 200, october.text
    assert october.headers["cache-control"] == "private, no-store"
    assert october.json() == {
        "month": "2026-10",
        "today": TODAY.isoformat(),
        "owner": False,
        "days": [
            {
                "date": SAT1.isoformat(),
                "kind": "regular",
                "title": None,
                "places": [{"name": "Зеленоград", "slug": park.external_key, "cancelled": False, "regular": False}],
                "rest": False,
            }
        ],
    }
    assert "Кузьминки" not in october.text
    november = _calendar_of(client, "cal-runner", "2026-11").json()
    assert [(day["date"], day["rest"], day["places"]) for day in november["days"]] == [(nov7.isoformat(), True, [])]

    # Хозяину — то же, что другим, и пометка owner (ссылка «Все мои планы»).
    client.cookies.clear()
    _login(client, db_session, telegram_id=runner.telegram_id)
    mine = _calendar_of(client, "cal-runner").json()
    assert mine["owner"] is True and [day["date"] for day in mine["days"]] == [SAT1.isoformat()]


@pytest.mark.parametrize("handle", ["²", "١٢", "99999999999999999999", "9223372036854775808", "a\x00b"])
def test_profile_routes_answer_404_on_junk_handle(client: TestClient, handle: str) -> None:
    """Хендл из адреса, который не разобрать, — «не найден», а не 500: «²» и
    «١٢» str.isdigit считает цифрами, номер больше bigint и NUL база не
    примет (ревью 28.10.2026, FUZZ-1)."""
    for path, params in (
        ("/api/planner/profile", {}),
        ("/api/planner/profile/tab", {}),
        ("/api/planner/profile/calendar", {"month": "2026-11"}),
    ):
        response = client.get(path, params={"handle": handle, **params})
        assert response.status_code == 404, (path, response.text)


def test_profile_plans_tab_state(client: TestClient, db_session: Session) -> None:
    """Пункт «Планы» профиля (правка 27.10.2026): активен, если впереди есть
    публичный план или публичное «Не бегаю»; иначе — «Публичных планов нет»;
    закрытый профиль — «Профиль закрыт»; хозяину — всегда активен."""
    runner = _login(client, db_session)
    runner.public_slug = "tab-runner"
    park = _location(db_session, name="Зеленоград")
    db_session.commit()

    def state(handle: str = "tab-runner") -> str:
        response = client.get("/api/planner/profile/tab", params={"handle": handle})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        return response.json()["state"]

    assert state() == "public"  # хозяин
    assert _save(client, park, SAT1, is_public=False).status_code == 200
    rest_day = date(2026, 11, 7)
    assert _rest(client, rest_day, is_public=False).status_code == 200
    _plan(db_session, runner, park, PAST_SAT)  # прошедший план не в счёт
    db_session.commit()
    client.cookies.clear()
    assert state() == "empty"

    # Публичное «Не бегаю» — уже есть что показать.
    db_session.query(RunPlan).filter(RunPlan.user_id == runner.id, RunPlan.plan_date == rest_day).update(
        {"is_public": True}
    )
    db_session.commit()
    assert state() == "public"
    db_session.query(RunPlan).filter(RunPlan.user_id == runner.id, RunPlan.plan_date == rest_day).update(
        {"is_public": False}
    )
    db_session.query(RunPlan).filter(RunPlan.user_id == runner.id, RunPlan.plan_date == SAT1).update(
        {"is_public": True}
    )
    db_session.commit()
    assert state() == "public"

    runner.profile_private = True
    db_session.commit()
    assert state() == "closed"
    assert client.get("/api/planner/profile/tab", params={"handle": "nobody-here"}).status_code == 404


def test_profile_calendar_hidden_profile_child_and_month(client: TestClient, db_session: Session) -> None:
    """Закрытый профиль — 404 чужим (хозяину — пустой календарь); ребёнку —
    пустой календарь; месяц — ГГГГ-ММ и не дальше года."""
    owner = _login(client, db_session)
    owner.public_slug = "cal-closed"
    owner.profile_private = True
    park = _location(db_session, name="Зеленоград")
    _plan(db_session, owner, park, SAT1)
    db_session.commit()
    mine = _calendar_of(client, "cal-closed").json()
    assert (mine["owner"], mine["days"]) == (True, [])
    client.cookies.clear()
    assert _calendar_of(client, "cal-closed").status_code == 404
    assert _calendar_of(client, "no-such-runner").status_code == 404

    kid = _public_user(db_session, "Кира", public_slug="cal-kid")
    _run(db_session, _event(db_session, _location(db_session), PAST_SAT, 3), _participant(db_session, kid), age="Ж10")
    _plan(db_session, kid, park, SAT1)
    db_session.add(RunPlan(user_id=kid.id, plan_date=SAT2, rest=True, is_public=True))
    db_session.commit()
    assert _calendar_of(client, "cal-kid").json()["days"] == []
    assert _profile(client, "cal-kid").json() is None

    assert _calendar_of(client, "cal-kid", "2027-11").status_code == 400
    assert _calendar_of(client, "cal-kid", "2025-09").status_code == 400
    assert _calendar_of(client, "cal-kid", "2026-13").status_code == 400
    # Последний месяц календаря дат — тоже «дальше года», а не 500 (CORE-3).
    assert _calendar_of(client, "cal-kid", "9999-12").status_code == 400
    assert _calendar_of(client, "cal-kid", "октябрь").status_code == 422
    # Прошлый месяц в пределах года — без дней: прошедшего в календаре нет.
    assert _calendar_of(client, "cal-kid", "2026-09").json()["days"] == []
