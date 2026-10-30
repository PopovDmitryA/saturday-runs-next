"""Админка «Планы»: доступ, сводка, нагрузка с постоянными, план → факт и лента
со всеми планами — приватные админ видит с пометкой."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import (
    Event,
    Location,
    NotificationDelivery,
    Participant,
    Platform,
    PlatformLink,
    RunPlan,
    RunPlanSeries,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.admin_plans_service import (
    NotStartDay,
    get_admin_plans_day,
    get_admin_plans_feed,
    get_admin_plans_overview,
)

ADMIN_TELEGRAM_ID = 9311
# Пятница; ближайшая суббота — 31.10, прошлые — 24.10 и 17.10.
TODAY = date(2026, 10, 30)
SAT1 = date(2026, 10, 31)
SAT2 = date(2026, 11, 7)
PAST1 = date(2026, 10, 17)
PAST2 = date(2026, 10, 24)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "app.services.start_calendar_service.moscow_today",
        "app.services.run_plan_service.moscow_today",
        "app.services.admin_plans_service.moscow_today",
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


def _login(client: TestClient, telegram_id: int) -> None:
    request_token = client.post("/api/auth/login-request").json()["request_token"]
    confirm = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)


def _platform(db: Session) -> Platform:
    return db.query(Platform).filter(Platform.code == "five_verst").one()


def _location(db: Session) -> Location:
    """Действующая локация: старт неделю назад — календарь суббот у неё есть."""
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=_platform(db).id,
        external_key=f"adminplans-{suffix}",
        name=f"Парк {suffix}",
        city="Москва",
        country="Россия",
    )
    db.add(location)
    db.flush()
    db.add(
        Event(
            platform_id=location.platform_id,
            location_id=location.id,
            external_event_key=f"adminplans-{suffix}-started",
            event_date=date(2026, 10, 10),
            event_number=40,
        )
    )
    db.flush()
    return location


def _user(db: Session, name: str, **fields: object) -> User:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), display_name=name, **fields)
    db.add(user)
    db.flush()
    return user


def _participant(db: Session, user: User) -> Participant:
    external = f"ap-{uuid4().hex[:10]}"
    participant = Participant(platform_id=_platform(db).id, external_user_id=external, display_name=user.display_name)
    db.add(participant)
    db.add(PlatformLink(
            user_id=user.id, platform_id=_platform(db).id, external_user_id=external, external_url=f"https://5verst.ru/{external}"
        ))
    db.flush()
    return participant


def _plan(
    db: Session, user: User, location: Location | None, on_date: date, *, created: datetime, public: bool = True
) -> RunPlan:
    plan = RunPlan(
        user_id=user.id,
        plan_date=on_date,
        location_key=f"location:{location.id}" if location else None,
        location_id=location.id if location else None,
        platform_code="five_verst" if location else None,
        rest=location is None,
        is_public=public,
        created_at=created,
    )
    db.add(plan)
    db.flush()
    return plan


def _event(db: Session, location: Location, on_date: date) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"adminplans-{uuid4().hex}",
        event_date=on_date,
    )
    db.add(event)
    db.flush()
    return event


@pytest.fixture
def world(db_session: Session) -> dict[str, object]:
    db = db_session
    home = _location(db)
    away = _location(db)
    anna = _user(db, "Анна Планова")
    boris = _user(db, "Борис Тайный")
    vera = _user(db, "Вера Постоянная")
    created = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    _plan(db, anna, home, SAT1, created=created)
    _plan(db, anna, home, PAST2, created=datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
    _plan(db, boris, away, SAT1, created=datetime(2026, 10, 29, 9, 0, tzinfo=UTC), public=False)
    _plan(db, boris, home, PAST2, created=datetime(2026, 10, 22, 9, 0, tzinfo=UTC), public=False)
    _plan(db, vera, None, SAT2, created=datetime(2026, 10, 28, 9, 0, tzinfo=UTC))
    db.add(
        RunPlanSeries(
            user_id=vera.id,
            location_key=f"location:{home.id}",
            location_id=home.id,
            starts_on=PAST1,
            is_public=True,
            created_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        )
    )
    db.flush()
    # 24.10 на домашней: Анна пробежала, Вера волонтёрила, Борис не пришёл.
    event = _event(db, home, PAST2)
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=_participant(db, anna).id,
            external_result_key=f"ap-{uuid4().hex}",
            finish_time_sec=1500,
        )
    )
    db.add(
        VolunteerResult(
            event_id=event.id, participant_id=_participant(db, vera).id, external_result_key=f"ap-{uuid4().hex}"
        )
    )
    db.add(NotificationDelivery(user_id=anna.id, kind="plan_reminders", dedupe_key=uuid4().hex, status="sent"))
    db.add(NotificationDelivery(user_id=boris.id, kind="plan_reminders", dedupe_key=uuid4().hex, status="failed"))
    db.flush()
    return {"home": home, "away": away, "anna": anna, "boris": boris, "vera": vera}


def test_admin_plans_requires_admin(client: TestClient, db_session: Session) -> None:
    assert client.get("/api/admin/plans").status_code == 401
    _login(client, int(uuid4().int % 10_000_000_000))
    assert client.get("/api/admin/plans").status_code == 403
    assert client.get("/api/admin/plans/feed").status_code == 403
    assert client.get("/api/admin/plans/day", params={"date": SAT1.isoformat()}).status_code == 403


def test_admin_plans_api_for_admin(client: TestClient, world: dict[str, object]) -> None:
    _login(client, ADMIN_TELEGRAM_ID)
    overview = client.get("/api/admin/plans", params={"period_days": 30})
    assert overview.status_code == 200
    assert overview.json()["summary"]["people_total"] >= 3
    feed = client.get("/api/admin/plans/feed", params={"person": "Борис Тайный"})
    assert feed.status_code == 200
    assert {item["is_public"] for item in feed.json()["items"]} == {False}


def test_overview_summary_load_and_facts(db_session: Session, world: dict[str, object]) -> None:
    payload = get_admin_plans_overview(db_session, period_days=30, today=TODAY)
    summary = payload["summary"]
    assert summary["people_total"] == 3
    assert summary["plans_future"] == 2
    assert summary["plans_past"] == 2
    assert summary["plans_public"] == 2
    assert (summary["series_active"], summary["series_sleeping"], summary["series_stopped"]) == (1, 0, 0)
    assert (summary["rest_total"], summary["rest_future"]) == (1, 1)
    # Впереди: Анна и Борис разовыми, Вера — постоянным.
    assert summary["people_future"] == 3

    # Нагрузка через planned_starts: 31.10 — двое разовых и суббота Веры,
    # 07.11 — у Веры «Не бегаю», вхождения нет.
    load = {day["date"]: day for day in payload["load"]}
    assert (load[SAT1]["people"], load[SAT1]["one_off"], load[SAT1]["regular"]) == (3, 2, 1)
    assert load[SAT2]["people"] == 0

    # Прошлое: 24.10 Анна пробежала, Борис не пришёл, Вера (постоянный)
    # волонтёрила; 17.10 у Веры — старта не было, протокола нет.
    facts = payload["facts"]
    assert (facts["ran"], facts["volunteered"], facts["missed"]) == (1, 1, 1)
    assert facts["unknown"] == 1

    top = {item["key"]: item for item in payload["top_locations"]}
    home_key = f"location:{world['home'].id}"  # type: ignore[attr-defined]
    assert top[home_key]["plans"] == 3 and top[home_key]["series"] == 1 and top[home_key]["people"] == 3
    reminders = next(item for item in payload["deliveries"] if item["kind"] == "plan_reminders")
    assert reminders["by_status"] == {"sent": 1, "failed": 1}
    assert sum(day["people"] for day in payload["daily"]) == 3
    assert sum(bucket["count"] for bucket in payload["lead_times"]) == 4


def test_feed_shows_private_plans_with_flag_and_filters(db_session: Session, world: dict[str, object]) -> None:
    boris = world["boris"]
    feed = get_admin_plans_feed(db_session, person=str(boris.serial_id), today=TODAY)  # type: ignore[attr-defined]
    assert feed["total"] == 2
    assert all(item["is_public"] is False for item in feed["items"])
    # Новые сверху.
    assert [item["on_date"] for item in feed["items"]] == [SAT1, PAST2]

    future = get_admin_plans_feed(db_session, person="Борис Тайный", future_only=True, today=TODAY)
    assert [item["on_date"] for item in future["items"]] == [SAT1]

    home = world["home"]
    at_home = get_admin_plans_feed(db_session, location_key=f"location:{home.id}", today=TODAY)  # type: ignore[attr-defined]
    kinds = sorted(item["kind"] for item in at_home["items"])
    assert kinds == ["plan", "plan", "plan", "series"]
    series = next(item for item in at_home["items"] if item["kind"] == "series")
    assert series["series_status"] == "active" and series["user_label"] == "Вера Постоянная"

    vera = get_admin_plans_feed(db_session, person="Вера Постоянная", today=TODAY)
    assert sorted(item["kind"] for item in vera["items"]) == ["rest", "series"]

    page = get_admin_plans_feed(db_session, person="Анна Планова", limit=1, offset=1, today=TODAY)
    assert page["total"] == 2 and len(page["items"]) == 1


def test_day_lists_people_by_location(db_session: Session, world: dict[str, object]) -> None:
    db = db_session
    gala = _user(db, "Галина Отдыхает", profile_private=True)
    _plan(db, gala, None, SAT1, created=datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    day = get_admin_plans_day(db, on_date=SAT1, today=TODAY)
    home, away = world["home"], world["away"]
    by_name = {location["name"]: location for location in day["locations"]}
    # Домашняя — двое (Анна разовым, Вера постоянным), выше чужой с одним Борисом.
    assert [location["name"] for location in day["locations"]] == [home.name, away.name]  # type: ignore[attr-defined]
    people = {person["label"]: person for person in by_name[home.name]["people"]}  # type: ignore[attr-defined]
    assert people["Анна Планова"]["regular"] is False and people["Анна Планова"]["is_public"] is True
    assert people["Вера Постоянная"]["regular"] is True
    boris = by_name[away.name]["people"][0]  # type: ignore[attr-defined]
    assert boris["label"] == "Борис Тайный" and boris["is_public"] is False
    assert by_name[home.name]["slug"]  # type: ignore[attr-defined]
    assert day["summary"] == {"people": 3, "locations": 2, "one_off": 2, "regular": 1, "private": 1, "rest": 1}
    assert [(person["label"], person["profile_private"]) for person in day["rest"]] == [("Галина Отдыхает", True)]
    assert (day["prev_date"], day["next_date"], day["is_past"]) == (PAST2, SAT2, False)

    # 07.11: у Веры «Не бегаю» — постоянный в этот день не бежит.
    later = get_admin_plans_day(db, on_date=SAT2, today=TODAY)
    assert later["locations"] == [] and [person["label"] for person in later["rest"]] == ["Вера Постоянная"]

    # Прошедшая дата тоже открывается: 24.10 — Анна, Борис и суббота Веры.
    past = get_admin_plans_day(db, on_date=PAST2, today=TODAY)
    assert past["is_past"] is True and past["summary"]["people"] == 3 and past["summary"]["regular"] == 1

    with pytest.raises(NotStartDay):
        get_admin_plans_day(db, on_date=TODAY, today=TODAY)


def test_day_api_rejects_weekday(client: TestClient, world: dict[str, object]) -> None:
    _login(client, ADMIN_TELEGRAM_ID)
    assert client.get("/api/admin/plans/day", params={"date": "2026-10-28"}).status_code == 400
    ok = client.get("/api/admin/plans/day", params={"date": SAT1.isoformat()})
    assert ok.status_code == 200, ok.text
    assert ok.json()["summary"]["people"] == 3
    feed = client.get("/api/admin/plans/feed", params={"plan_date": SAT1.isoformat()})
    assert feed.status_code == 200 and feed.json()["total"] == 3


def test_feed_filters_by_plan_date(db_session: Session, world: dict[str, object]) -> None:
    on_sat1 = get_admin_plans_feed(db_session, plan_date=SAT1, today=TODAY)
    # Два разовых на 31.10 и постоянный Веры, действующий с 17.10.
    assert sorted(item["kind"] for item in on_sat1["items"]) == ["plan", "plan", "series"]
    on_sat2 = get_admin_plans_feed(db_session, plan_date=SAT2, today=TODAY)
    assert sorted(item["kind"] for item in on_sat2["items"]) == ["rest", "series"]
    before = get_admin_plans_feed(db_session, plan_date=date(2026, 10, 10), today=TODAY)
    assert before["total"] == 0
