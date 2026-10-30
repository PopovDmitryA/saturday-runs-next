"""Постоянный план («каждую субботу»): правило и исключения, а не строки на
каждую неделю (решения Дмитрия 25.10.2026 по странице «Планы для постоянных»).

Вхождения — даты календаря точки (planned_starts); исключения — «Не бегаю» и
разовый план в другой локации; сон после трёх стартов без человека и
пробуждение по протоколу; подсказка «9 из 10»; все читатели планов видят
постоянных."""

from __future__ import annotations

import ast
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

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
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationOrganizerAccess,
    NotificationDelivery,
    RunPlan,
    RunPlanSeries,
    StartSpecialDate,
    User,
)
from app.services import notification_channels_service as channels
from app.services import notification_service as notify
from app.services import plan_notification_service as plan_notify
from app.services import plan_series_service, planner_guests_service, run_plan_service
from app.services.dashboard_service import ANALYTICS_VERSION
from app.services.location_cancellation_notify import CancellationChange
from app.services.notification_channels_service import CheckOutcome
from app.services.organizer_access_service import invalidate_organizer_locations_cache
from app.services.planned_starts_service import planned_starts
from app.services.run_plan_service import location_plans_block
from tests.test_run_plans import (
    ADMIN_TELEGRAM_ID,
    HOLIDAY,
    PAST_SAT,
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

SAT3 = date(2026, 11, 7)
SAT4 = date(2026, 11, 14)
# Будняя особая дата другой системы и будняя без прошлогоднего старта.
OTHER_SYSTEM_DAY = date(2026, 11, 11)
UNCONFIRMED_DAY = date(2026, 11, 18)
# Старт перенесли с субботы 07.11 на пятницу или воскресенье рядом.
MOVED_FRIDAY = date(2026, 11, 6)
MOVED_SUNDAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "app.services.start_calendar_service.moscow_today",
        "app.services.run_plan_service.moscow_today",
        "app.services.plan_series_service.moscow_today",
        "app.services.planner_guests_service.moscow_today",
        "app.api.routes.planner.moscow_today",
        "app.api.routes.organizer.moscow_today",
    ):
        monkeypatch.setattr(target, lambda: TODAY)


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    queued: list[UUID] = []
    monkeypatch.setattr(notify, "enqueue_delivery", lambda delivery_id: queued.append(delivery_id) or True)
    return queued


@pytest.fixture(autouse=True)
def _notify_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        app_secret_key="test-secret-key",
        app_base_url="https://run5k.test",
        telegram_bot_token="t",
        telegram_bot_username="test_bot",
    )
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(channels, "get_settings", lambda: settings)
    monkeypatch.setattr(plan_notify, "get_settings", lambda: settings)
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=True))


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


# ---------------------------------------------------------------- помощники


def _msk(on_date: date, hour: int = 12) -> datetime:
    """Полдень (или час) по Москве в этот день — в UTC."""
    return datetime(on_date.year, on_date.month, on_date.day, hour - 3, 0, tzinfo=UTC)


def _series(
    db: Session,
    user: User,
    location: Location,
    starts_on: date,
    *,
    until: date | None = None,
    created_on: date | None = None,
    **fields: object,
) -> RunPlanSeries:
    series = RunPlanSeries(
        user_id=user.id,
        location_key=_key(location),
        location_id=location.id,
        starts_on=starts_on,
        until=until,
        created_at=_msk(created_on or starts_on - timedelta(days=6)),
        sleep_history=fields.pop("sleep_history", []),
        is_public=fields.pop("is_public", True),
        **fields,
    )
    db.add(series)
    db.flush()
    return series


def _special(db: Session, on_date: date, platforms: list[str], title: str = "Особый старт") -> StartSpecialDate:
    special = db.query(StartSpecialDate).filter_by(start_date=on_date).one_or_none()
    if special is None:
        special = StartSpecialDate(start_date=on_date, title=title, platforms=platforms)
        db.add(special)
    else:
        special.platforms = platforms
    db.flush()
    return special


def _put_series(client: TestClient, location: Location, **body: object) -> object:
    payload = {"key": _key(location)}
    payload.update({key: value.isoformat() if isinstance(value, date) else value for key, value in body.items()})
    return client.put("/api/planner/series", json=payload)


def _my(client: TestClient, date_from: date, date_to: date) -> list[dict[str, object]]:
    response = client.get("/api/planner/my", params={"from": date_from.isoformat(), "to": date_to.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _block(client: TestClient, location: Location) -> dict[str, object]:
    response = client.get("/api/planner/location", params={"key": _key(location)})
    assert response.status_code == 200, response.text
    return response.json()


def _entries(db: Session, user: User, date_from: date, date_to: date, **extra: object) -> list[object]:
    return planned_starts(db, today=TODAY, date_from=date_from, date_to=date_to, user_ids=[user.id], **extra)


def _dates(entries: list[object]) -> list[date]:
    return [entry.plan_date for entry in entries]  # type: ignore[attr-defined]


def _subscribe(db: Session, user: User) -> None:
    user.telegram_chat_id = user.telegram_id
    db.commit()
    notify.set_channel_enabled(db, user, "telegram", True)
    db.commit()


def _sent(db: Session, user: User, kind: str) -> list[NotificationDelivery]:
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.user_id == user.id, NotificationDelivery.kind == kind)
        .all()
    )


def _identity(location: Location) -> object:
    return SimpleNamespace(
        identity_key=_key(location), locations=[(location, "five_verst")], slug=location.external_key, name=location.name
    )


# ---------------------------------------------------------------- вхождения


def test_series_occurrences_are_calendar_dates(db_session: Session) -> None:
    """Вхождения — субботы и ВСЕ особые даты систем точки, что завёл админ
    (Q08=A; 26.10.2026: «особые даты всегда проставляются вручную в админке,
    не надо никакой логики здесь городить» — без проверки, был ли старт год
    назад); особая дата чужой системы — нет. Админ убрал особую дату —
    вхождение исчезло само."""
    park = _location(db_session, name="Зеленоград")
    _special(db_session, HOLIDAY, ["five_verst"], "День народного единства")
    _special(db_session, OTHER_SYSTEM_DAY, ["s95"])
    _special(db_session, UNCONFIRMED_DAY, ["five_verst"])
    runner = _user(db_session, "Постоянная")
    _series(db_session, runner, park, SAT1)

    window = _dates(_entries(db_session, runner, TODAY, date(2026, 11, 21)))
    assert window == [SAT1, SAT2, HOLIDAY, SAT3, SAT4, UNCONFIRMED_DAY, date(2026, 11, 21)]
    # Прошедшая будняя особая дата без прошлогоднего старта — тоже в истории.
    history = _series(db_session, _user(db_session, "Давняя"), park, date(2026, 9, 5), until=date(2026, 10, 20))
    _special(db_session, date(2026, 9, 9), ["five_verst"], "Будний старт")
    past = _dates(_entries(db_session, db_session.get(User, history.user_id), date(2026, 9, 5), date(2026, 9, 12)))
    assert past == [date(2026, 9, 5), date(2026, 9, 9), date(2026, 9, 12)]

    run_plan_service.admin_delete_special_date(
        db_session, db_session.query(StartSpecialDate).filter_by(start_date=HOLIDAY).one().id
    )
    assert HOLIDAY not in _dates(_entries(db_session, runner, TODAY, date(2026, 11, 21)))


def test_saturday_stays_next_to_moved_start(client: TestClient, db_session: Session) -> None:
    """Суббота в постоянном плане всегда: перенос старта на пятницу или
    воскресенье (особая дата рядом) её не убирает — «суббота всегда
    существует» (Дмитрий 26.10.2026). Перенесённый старт — ещё одно вхождение."""
    park = _location(db_session, name="Зеленоград")
    _special(db_session, MOVED_FRIDAY, ["five_verst"], "Перенос на пятницу")
    _special(db_session, MOVED_SUNDAY, ["five_verst"], "Перенос на воскресенье")
    runner = _user(db_session, "Постоянная")
    _series(db_session, runner, park, SAT1)
    window = _dates(_entries(db_session, runner, date(2026, 11, 1), SAT4))
    assert window == [MOVED_FRIDAY, SAT3, MOVED_SUNDAY, SAT4]
    # Календарь локации и окно постоянного плана видят ту же субботу.
    dates = [item["date"] for item in run_plan_service.location_start_dates(db_session, _key(park))["dates"]]
    assert {MOVED_FRIDAY, SAT3, MOVED_SUNDAY} <= set(dates)
    _login(client, db_session)
    db_session.commit()
    preview = client.get(
        "/api/planner/series/preview", params={"key": _key(park), "starts_on": SAT1.isoformat(), "limit": 8}
    ).json()
    assert [item["date"] for item in preview["dates"]][:6] == [
        SAT1.isoformat(),
        SAT2.isoformat(),
        MOVED_FRIDAY.isoformat(),
        SAT3.isoformat(),
        MOVED_SUNDAY.isoformat(),
        SAT4.isoformat(),
    ]


def test_series_until_limits_occurrences(client: TestClient, db_session: Session) -> None:
    """V1 «до…»: последняя дата включительно, дальше вхождений нет."""
    park = _location(db_session, name="Кузьминки")
    _login(client, db_session)
    db_session.commit()
    response = _put_series(client, park, until=SAT2)
    assert response.status_code == 200, response.text
    series = response.json()["series"]
    assert (series["starts_on"], series["until"], series["expired"]) == (SAT1.isoformat(), SAT2.isoformat(), False)
    assert [item["plan_date"] for item in _my(client, TODAY, SAT4)] == [SAT1.isoformat(), SAT2.isoformat()]
    assert all(item["id"] is None and item["series"] == {"id": series["id"]} for item in _my(client, TODAY, SAT4))


def test_one_active_series_per_user(client: TestClient, db_session: Session) -> None:
    """Вторая точка — 409 с вопросом; replace — старая останавливается, её
    прошлые субботы остаются; та же точка — правка срока."""
    home = _location(db_session, name="Кузьминки")
    other = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    old = _series(db_session, user, home, date(2026, 9, 26))
    db_session.commit()

    conflict = _put_series(client, other)
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["detail"] == (
        "Ваш постоянный план сейчас: Кузьминки. Заменить на Зеленоград? Прошлые субботы останутся в истории."
    )
    assert body["series"]["location"]["name"] == "Кузьминки"

    replaced = _put_series(client, other, replace=True)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["replaced"]["name"] == "Кузьминки"
    db_session.refresh(old)
    assert old.stopped_at is not None and old.until == TODAY - timedelta(days=1)
    items = _my(client, date(2026, 9, 20), SAT2)
    places = [(item["plan_date"], item["location"]["name"]) for item in items]
    assert places == [
        ("2026-09-26", "Кузьминки"),
        ("2026-10-03", "Кузьминки"),
        ("2026-10-10", "Кузьминки"),
        ("2026-10-17", "Кузьминки"),
        (SAT1.isoformat(), "Зеленоград"),
        (SAT2.isoformat(), "Зеленоград"),
    ]

    # Та же точка — правка срока, серия та же.
    same = _put_series(client, other, until=SAT3)
    assert same.status_code == 200
    assert same.json()["series"]["id"] == replaced.json()["series"]["id"]
    assert same.json()["series"]["until"] == SAT3.isoformat()
    assert db_session.query(RunPlanSeries).filter_by(user_id=user.id, stopped_at=None).count() == 1


def test_expired_series_restarts_without_backfill(client: TestClient, db_session: Session) -> None:
    """Кончившийся срок V1 «Продлить» после паузы — новая серия с сегодня: прошедший
    промежуток задним числом вхождениями не становится."""
    park = _location(db_session, name="Кузьминки")
    user = _login(client, db_session)
    old = _series(db_session, user, park, date(2026, 9, 12), until=date(2026, 10, 3))
    db_session.commit()
    response = _put_series(client, park)
    assert response.status_code == 200, response.text
    db_session.refresh(old)
    assert old.stopped_at is not None
    dates = [item["plan_date"] for item in _my(client, date(2026, 9, 1), SAT2)]
    assert "2026-10-10" not in dates and "2026-10-17" not in dates
    assert dates[-2:] == [SAT1.isoformat(), SAT2.isoformat()]


def test_conflict_text_for_paused_and_missing_location(client: TestClient, db_session: Session) -> None:
    """Точка старой серии на паузе — честный вопрос; точка пропала — старая
    серия останавливается молча."""
    paused = _location(db_session, name="Кузьминки")
    other = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    old = _series(db_session, user, paused, SAT1)
    paused.is_paused = True
    db_session.commit()
    conflict = _put_series(client, other)
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Локация «Кузьминки» сейчас не проводит старты. Сделать постоянной «Зеленоград»?"

    old.location_key = "location:00000000-0000-0000-0000-000000000000"
    old.location_id = None
    db_session.commit()
    assert _put_series(client, other).status_code == 200


def test_rest_skips_occurrence_everywhere(client: TestClient, db_session: Session) -> None:
    """«Пропущу» = «Не бегаю» на день: вхождения нет в блоке, в «Планах», в
    поправке юбилеев; снятие отметки возвращает субботу."""
    park = _location(db_session, name="Кузьминки")
    user = _login(client, db_session)
    user.public_slug = "regular-skip"
    db_session.commit()
    assert _put_series(client, park).status_code == 200

    assert client.put(f"/api/planner/rest/{SAT1.isoformat()}").status_code == 200
    assert SAT1 not in _dates(_entries(db_session, user, TODAY, SAT2))
    ahead = planner_guests_service.plans_ahead(db_session, [user.id], today=TODAY, until=SAT2)[user.id]
    assert [plan.plan_date for plan in ahead.plans] == [SAT2]
    my_series = _block(client, park)["my_series"]
    assert my_series["skipped"] == [SAT1.isoformat()] and my_series["next_date"] == SAT2.isoformat()

    assert client.delete(f"/api/planner/rest/{SAT1.isoformat()}").status_code == 204
    assert _dates(_entries(db_session, user, TODAY, SAT2)) == [SAT1, SAT2]


def test_trip_displaces_and_returns(client: TestClient, db_session: Session) -> None:
    """Поездка в субботу вытесняет постоянный план (Q07), убрал — суббота
    вернулась; «Убрать · Не бегаю в этот день» — атомарно ставит отметку."""
    home = _location(db_session, name="Зеленоград")
    trip = _location(db_session, name="Кусково")
    user = _login(client, db_session)
    db_session.commit()
    series_id = _put_series(client, home).json()["series"]["id"]

    saved = client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT2.isoformat()})
    assert saved.status_code == 200, saved.text
    assert saved.json()["displaced"]["name"] == "Зеленоград"
    assert saved.json()["plan"]["displaces"]["id"] == series_id
    plan_id = saved.json()["plan"]["id"]
    on_sat2 = [item for item in _my(client, SAT2, SAT2) if item["kind"] == "plan"]
    assert [item["location"]["name"] for item in on_sat2] == ["Кусково"]
    assert _block(client, home)["my_series"]["away"] == [
        {"date": SAT2.isoformat(), "location": {"key": _key(trip), "slug": trip.external_key, "name": "Кусково", "platform_code": None}}
    ]

    assert client.delete(f"/api/planner/plans/{plan_id}").status_code == 204
    back = _my(client, SAT2, SAT2)
    assert [(item["location"]["name"], item["id"]) for item in back] == [("Зеленоград", None)]

    again = client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT2.isoformat()}).json()
    assert client.delete(f"/api/planner/plans/{again['plan']['id']}", params={"rest": "true"}).status_code == 204
    (row,) = _my(client, SAT2, SAT2)
    assert (row["kind"], row["rest"]) == ("day", True)
    assert db_session.query(RunPlan).filter(RunPlan.user_id == user.id, RunPlan.location_key.isnot(None)).count() == 0


def test_trip_displaces_on_special_day_too(client: TestClient, db_session: Session) -> None:
    """Особый день — по тому же правилу (К2а): поездка 4 ноября вытесняет дом, в
    лимит вхождение не идёт."""
    home = _location(db_session, name="Зеленоград")
    trip = _location(db_session, name="Кусково")
    for park in (home, trip):
        _event(db_session, park, date(2025, 11, 4), 50)
    _special(db_session, HOLIDAY, ["five_verst"])
    user = _login(client, db_session)
    db_session.commit()
    assert _put_series(client, home).status_code == 200
    assert HOLIDAY in _dates(_entries(db_session, user, TODAY, SAT3))

    saved = client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": HOLIDAY.isoformat()})
    assert saved.status_code == 200, saved.text
    assert saved.json()["displaced"]["name"] == "Зеленоград"
    on_day = _entries(db_session, user, HOLIDAY, HOLIDAY)
    assert [(entry.key, entry.plan is not None) for entry in on_day] == [(_key(trip), True)]


def test_keep_series_on_special_day_pins_home(client: TestClient, db_session: Session) -> None:
    """«+ ещё локация» на особую дату (решение 27.10.2026): keep_series не
    вытесняет дом, а закрепляет его разовым планом той же транзакцией — дом
    и поездка оба в планах, и дом считается в лимит три. На обычную субботу
    флаг ничего не делает: там поездка вытесняет, как всегда."""
    home = _location(db_session, name="Зеленоград")
    trip = _location(db_session, name="Кусково")
    third = _location(db_session, name="Коломенское")
    fourth = _location(db_session, name="Битца")
    for park in (home, trip, third, fourth):
        _event(db_session, park, date(2025, 11, 4), 50)
    _special(db_session, HOLIDAY, ["five_verst"])
    _login(client, db_session)
    db_session.commit()
    assert _put_series(client, home, is_public=False).status_code == 200

    saved = client.post(
        "/api/planner/plans", json={"key": _key(trip), "plan_date": HOLIDAY.isoformat(), "keep_series": True}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["displaced"] is None
    # Обе строки — одной транзакцией, created_at у них один: порядок не важен.
    on_day = {item["location"]["name"]: item for item in _my(client, HOLIDAY, HOLIDAY)}
    assert {name: (item["id"] is not None, item["series"] is not None) for name, item in on_day.items()} == {
        "Зеленоград": (True, True),
        "Кусково": (True, False),
    }
    # Закреплённый дом — с публичностью постоянного плана.
    assert on_day["Зеленоград"]["is_public"] is False
    # Дом в лимите: третий план ещё встаёт, четвёртый — нет.
    assert _post_plan(client, third, HOLIDAY)["plan"]["location"]["name"] == "Коломенское"
    full = client.post("/api/planner/plans", json={"key": _key(fourth), "plan_date": HOLIDAY.isoformat()})
    assert full.status_code == 409

    # Обычная суббота: флаг молчит, поездка вытесняет дом.
    sat = client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT2.isoformat(), "keep_series": True})
    assert sat.status_code == 200 and sat.json()["displaced"]["name"] == "Зеленоград"
    assert [item["location"]["name"] for item in _my(client, SAT2, SAT2)] == ["Кусково"]


def _holiday_trips(client: TestClient, db: Session) -> tuple[Location, Location, Location, User, str]:
    """Постоянный план в Зеленограде, 4 ноября — особый день (лимит три плана)."""
    home = _location(db, name="Зеленоград")
    trip = _location(db, name="Кусково")
    other_trip = _location(db, name="Коломенское")
    for park in (home, trip, other_trip):
        _event(db, park, date(2025, 11, 4), 50)
    _special(db, HOLIDAY, ["five_verst"])
    user = _login(client, db)
    db.commit()
    series_id = _put_series(client, home).json()["series"]["id"]
    return home, trip, other_trip, user, series_id


def _post_plan(client: TestClient, location: Location, on_date: date) -> dict[str, object]:
    response = client.post("/api/planner/plans", json={"key": _key(location), "plan_date": on_date.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


def test_second_trip_does_not_promise_return(client: TestClient, db_session: Session) -> None:
    """Две поездки в один день: суббота вытеснена одна (away — одной строкой), но
    «вернётся, если уберёте» не обещает ни одна — после «Убрать» одной её держит
    вторая. Убрали одну — у оставшейся обещание снова верное."""
    home, trip, other_trip, _user_row, series_id = _holiday_trips(client, db_session)
    first = _post_plan(client, trip, HOLIDAY)
    assert first["displaced"]["name"] == "Зеленоград"
    second = _post_plan(client, other_trip, HOLIDAY)
    assert second["displaced"] is None and second["plan"]["displaces"] is None
    on_day = [item for item in _my(client, HOLIDAY, HOLIDAY) if item["kind"] == "plan"]
    assert sorted(item["location"]["name"] for item in on_day) == ["Коломенское", "Кусково"]
    assert all(item["displaces"] is None for item in on_day)
    away = _block(client, home)["my_series"]["away"]
    assert [item["date"] for item in away] == [HOLIDAY.isoformat()]

    assert client.delete(f"/api/planner/plans/{first['plan']['id']}").status_code == 204
    (left,) = [item for item in _my(client, HOLIDAY, HOLIDAY) if item["kind"] == "plan"]
    assert left["location"]["name"] == "Коломенское" and left["displaces"]["id"] == series_id


def test_trip_next_to_own_plan_displaces_nothing(client: TestClient, db_session: Session) -> None:
    """План на свою точку держит субботу сам: поездка в тот же день её не
    вытесняет и возврата не обещает; окно «каждую субботу» дату не отнимает."""
    home, trip, _other_trip, _user_row, series_id = _holiday_trips(client, db_session)
    own = _post_plan(client, home, HOLIDAY)
    assert own["plan"]["series"] == {"id": series_id}
    saved = _post_plan(client, trip, HOLIDAY)
    assert saved["displaced"] is None and saved["plan"]["displaces"] is None
    block = _block(client, home)
    assert block["my_series"]["away"] == []
    day = next(day for day in block["dates"] if day["date"] == HOLIDAY.isoformat())
    assert [person["regular"] for person in day["people"]] == [True]
    preview = client.get("/api/planner/series/preview", params={"key": _key(home), "limit": 8}).json()
    assert next(item for item in preview["dates"] if item["date"] == HOLIDAY.isoformat())["state"] == "on"


def test_replace_first_removes_every_trip_of_the_day(client: TestClient, db_session: Session) -> None:
    """«Заменить» первую дату убирает все поездки этого дня: одна оставшаяся
    держала бы субботу, и план начался бы не с выбранной даты."""
    home, trip, other_trip, user, _series_id = _holiday_trips(client, db_session)
    assert client.delete("/api/planner/series").status_code == 204
    _post_plan(client, trip, HOLIDAY)
    _post_plan(client, other_trip, HOLIDAY)
    assert _put_series(client, home, starts_on=HOLIDAY, replace_first=True).status_code == 200
    assert [entry.key for entry in _entries(db_session, user, HOLIDAY, HOLIDAY)] == [_key(home)]


def test_sleeping_series_is_not_displaced_and_asks_to_resume(client: TestClient, db_session: Session) -> None:
    """К14 и К15: поездка при спящем плане не обещает «вернётся», а «Планирую»
    на свою точку предлагает «Продолжить», но молча не будит."""
    home = _location(db_session, name="Зеленоград")
    trip = _location(db_session, name="Кусково")
    user = _login(client, db_session)
    _series(
        db_session,
        user,
        home,
        date(2026, 9, 26),
        sleeping_since=date(2026, 10, 11),
        sleep_missed=[date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10)],
    )
    db_session.commit()
    trip_saved = client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT1.isoformat()}).json()
    assert trip_saved["displaced"] is None and trip_saved["series_sleeping"] is False
    home_saved = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT2.isoformat()}).json()
    assert home_saved["series_sleeping"] is True and home_saved["plan"]["series"] is None
    assert db_session.query(RunPlanSeries).filter_by(user_id=user.id).one().sleeping_since == date(2026, 10, 11)


def test_one_off_at_home_is_absorbed(client: TestClient, db_session: Session) -> None:
    """Разовый план на свою точку — тот же план, не дубль: для чужих — постоянный."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "absorbed"
    db_session.commit()
    series_id = _put_series(client, home).json()["series"]["id"]
    saved = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT1.isoformat()}).json()
    assert saved["plan"]["series"] == {"id": series_id} and saved["displaced"] is None

    (item,) = _my(client, SAT1, SAT1)
    assert item["id"] == saved["plan"]["id"] and item["series"] == {"id": series_id}
    day = next(day for day in location_plans_block(db_session, _key(home), None)["dates"] if day["date"] == SAT1)
    assert day["total"] == 1
    assert [(person["handle"], person["regular"]) for person in day["people"]] == [("absorbed", True)]


def test_series_publicity_is_one_for_the_whole_plan(client: TestClient, db_session: Session) -> None:
    """Один выбор на весь постоянный план (27.10.2026): умолчание — как
    профиль; правка — тот же PUT; выбор переходит и на разовые планы на его
    точке в его даты, чтобы «Остановить» не открыло их по старому выбору."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "regular-public"
    db_session.commit()
    saved = _put_series(client, home)
    assert saved.status_code == 200, saved.text
    assert saved.json()["series"]["is_public"] is True and "needs_consent" not in saved.json()
    # Разовый план на точке серии в её дату: выбор серии, а не профиля.
    absorbed = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT2.isoformat()})
    assert absorbed.json()["plan"]["is_public"] is True

    def regulars_on(on_date: date) -> tuple[list[str], int]:
        day = next(day for day in _block(client, home)["dates"] if day["date"] == on_date.isoformat())
        return [person["handle"] for person in day["people"] if person["regular"]], day["private"]

    assert regulars_on(SAT1) == (["regular-public"], 0)
    private = _put_series(client, home, is_public=False)
    assert private.json()["series"]["is_public"] is False
    assert regulars_on(SAT1) == ([], 1)
    items = {item["plan_date"]: item for item in _my(client, SAT1, SAT2)}
    assert (items[SAT1.isoformat()]["is_public"], items[SAT2.isoformat()]["is_public"]) == (False, False)
    row = db_session.query(RunPlan).filter_by(user_id=user.id, plan_date=SAT2).one()
    db_session.refresh(row)
    assert row.is_public is False
    # Новый разовый план на точке приватного постоянного — тоже приватный.
    third = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT3.isoformat()})
    assert third.json()["plan"]["is_public"] is False
    # Без поля правка срока выбор не меняет.
    assert _put_series(client, home, until=SAT4).json()["series"]["is_public"] is False


def test_absorbed_plan_publicity_is_changed_in_the_series(client: TestClient, db_session: Session) -> None:
    """Разовый план на точке постоянного в его дату чужие видят вхождением: свой
    «Приватно» у него ничего бы не спрятал. Другой выбор — 409 с объяснением,
    а не тихая запись; тот же или без поля — можно (ревью 27.10.2026, PRIV-2)."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "rv-absorb"
    db_session.commit()
    assert _put_series(client, home, is_public=True).status_code == 200
    body = {"key": _key(home), "plan_date": SAT2.isoformat()}
    saved = client.post("/api/planner/plans", json=body)
    assert saved.status_code == 200 and saved.json()["plan"]["series"] is not None

    hidden = client.post("/api/planner/plans", json={**body, "is_public": False})
    assert hidden.status_code == 409
    assert hidden.json()["detail"] == run_plan_service.SERIES_OWNS_PUBLICITY
    row = db_session.query(RunPlan).filter_by(user_id=user.id, plan_date=SAT2).one()
    db_session.refresh(row)
    assert row.is_public is True
    assert client.post("/api/planner/plans", json={**body, "is_public": True}).status_code == 200

    def shown_on(on_date: date) -> bool:
        """Суббота видна чужим: календарь профиля у хозяина — тот же, что у всех."""
        response = client.get("/api/planner/profile/calendar", params={"handle": "rv-absorb", "month": "2026-10"})
        return on_date.isoformat() in {day["date"] for day in response.json()["days"]}

    assert shown_on(SAT2)
    # Поменять — в постоянном плане: выбор уходит и на эту субботу.
    assert _put_series(client, home, is_public=False).status_code == 200
    assert not shown_on(SAT2)
    assert client.post("/api/planner/plans", json={**body, "is_public": False}).status_code == 200


def _profile_dates(client: TestClient, handle: str, *months: str) -> set[str]:
    """Даты календаря «Планы» профиля — у хозяина тот же, что у всех."""
    found: set[str] = set()
    for month in months:
        response = client.get("/api/planner/profile/calendar", params={"handle": handle, "month": month})
        assert response.status_code == 200, response.text
        found |= {day["date"] for day in response.json()["days"]}
    return found


def test_private_plan_stays_private_when_series_wakes(client: TestClient, db_session: Session) -> None:
    """Приватный разовый план на точке спящего постоянного остаётся приватным
    после «Продолжить»: постоянный поглощает его без нового выбора человека
    (ревью 28.10.2026, PRIV-F1). Открывает дату только явное «Публично» в
    постоянном плане."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "rv-private-wake"
    _series(
        db_session,
        user,
        home,
        date(2026, 9, 26),
        sleeping_since=date(2026, 10, 11),
        sleep_missed=[date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10)],
    )
    db_session.commit()
    saved = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT2.isoformat(), "is_public": False})
    assert saved.status_code == 200, saved.text
    assert saved.json()["series_sleeping"] is True

    assert client.post("/api/planner/series/resume").status_code == 200
    assert _profile_dates(client, "rv-private-wake", "2026-10") == {SAT1.isoformat()}
    calendar = run_plan_service.organizer_calendar(db_session, _identity(home), SAT2)
    day = next(day for day in calendar["days"] if day["date"] == SAT2)
    assert (day["regulars"]["people"], day["regulars"]["private"]) == ([], 1)
    guests = planner_guests_service.organizer_guests(db_session, _identity(home))
    sat2 = next(item for item in guests["dates"] if item["date"] == SAT2)
    assert sat2["regulars"]["handles"] == [] and sat2["regulars"]["private"] == 1
    items = {item["plan_date"]: item["is_public"] for item in _my(client, SAT1, SAT2)}
    assert items == {SAT1.isoformat(): True, SAT2.isoformat(): False}

    assert _put_series(client, home, is_public=True).status_code == 200
    assert _profile_dates(client, "rv-private-wake", "2026-10") == {SAT1.isoformat(), SAT2.isoformat()}


def test_private_plan_stays_private_under_series_from_hint(client: TestClient, db_session: Session) -> None:
    """Постоянный план одним нажатием из подсказки (без is_public) — публичный,
    как профиль, но приватный разовый план на его точке не открывает (PRIV-F1)."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "rv-private-hint"
    db_session.commit()
    body = {"key": _key(home), "plan_date": SAT2.isoformat(), "is_public": False}
    assert client.post("/api/planner/plans", json=body).status_code == 200
    saved = _put_series(client, home)
    assert saved.status_code == 200 and saved.json()["series"]["is_public"] is True
    assert _profile_dates(client, "rv-private-hint", "2026-10") == {SAT1.isoformat()}


def test_returning_home_after_skip_or_trip_follows_the_series(client: TestClient, db_session: Session) -> None:
    """Приватный постоянный план, открытый профиль: «Планирую» домой на
    субботу с «Пропущу» или с поездкой («Заменить план») — окно шлёт умолчание
    профиля, и 409 «поменяйте в постоянном плане» ронял бы возврат домой
    (ревью 28.10.2026, CORE-2). План встаёт приватным, как постоянный."""
    home = _location(db_session, name="Зеленоград")
    trip = _location(db_session, name="Кусково")
    user = _login(client, db_session)
    user.public_slug = "rv-back-home"
    db_session.commit()
    assert _put_series(client, home, is_public=False).status_code == 200
    assert client.put(f"/api/planner/rest/{SAT3.isoformat()}").status_code == 200
    assert client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT4.isoformat()}).status_code == 200

    back = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT3.isoformat(), "is_public": True})
    assert back.status_code == 200, back.text
    assert back.json()["plan"]["is_public"] is False and back.json()["plan"]["series"] is not None
    replaced = client.post(
        "/api/planner/plans",
        json={"key": _key(home), "plan_date": SAT4.isoformat(), "is_public": True, "replace_key": _key(trip)},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["plan"]["is_public"] is False
    db_session.expire_all()
    rows = {
        row.plan_date: (row.location_id, row.is_public)
        for row in db_session.query(RunPlan).filter(RunPlan.user_id == user.id)
    }
    assert rows == {SAT3: (home.id, False), SAT4: (home.id, False)}
    assert _profile_dates(client, "rv-back-home", "2026-11") == set()

    # Публичный постоянный, «Приватно» при возврате домой — спрячет только этот день.
    assert _put_series(client, home, is_public=True).status_code == 200
    assert client.put(f"/api/planner/rest/{SAT3.isoformat()}", json={"replace": True}).status_code == 200
    hidden = client.post("/api/planner/plans", json={"key": _key(home), "plan_date": SAT3.isoformat(), "is_public": False})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["plan"]["is_public"] is False
    assert _profile_dates(client, "rv-back-home", "2026-11") == {SAT4.isoformat(), date(2026, 11, 21).isoformat(), date(2026, 11, 28).isoformat()}


def test_skipped_saturday_of_private_series_stays_private(client: TestClient, db_session: Session) -> None:
    """«Пропущу» у приватного постоянного — приватная отметка: в профиле не
    всплывает «не бегает» на его субботах (ревью 27.10.2026, PRIV-3). Вне
    дат плана умолчание — как профиль."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "rv-skip"
    db_session.commit()
    saved = _put_series(client, home, is_public=False, until=SAT3, skip=[SAT2.isoformat()])
    assert saved.status_code == 200, saved.text
    assert client.put(f"/api/planner/rest/{SAT3.isoformat()}").status_code == 200
    assert client.put(f"/api/planner/rest/{SAT4.isoformat()}").status_code == 200
    db_session.expire_all()
    rests = {
        row.plan_date: row.is_public
        for row in db_session.query(RunPlan).filter(RunPlan.user_id == user.id, RunPlan.rest.is_(True))
    }
    assert rests == {SAT2: False, SAT3: False, SAT4: True}
    calendar = {}
    for month in ("2026-10", "2026-11"):
        response = client.get("/api/planner/profile/calendar", params={"handle": "rv-skip", "month": month})
        calendar.update({day["date"]: day["rest"] for day in response.json()["days"]})
    assert calendar == {SAT4.isoformat(): True}


def test_closed_profile_series_is_private(client: TestClient, db_session: Session) -> None:
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.profile_private = True
    db_session.commit()
    assert _put_series(client, home).json()["series"]["is_public"] is False
    assert _put_series(client, home, is_public=True).json()["series"]["is_public"] is False


def test_v3_skip_and_unskip_dates(client: TestClient, db_session: Session) -> None:
    """Окно V3: снятые галочки — «Не бегаю», возвращённые — снять; дату, которую
    не отметить, сервер возвращает, а не роняет сохранение (К5)."""
    home = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    db_session.commit()
    response = _put_series(client, home, skip=[SAT2.isoformat(), date(2026, 10, 28).isoformat()])
    assert response.status_code == 200, response.text
    assert response.json()["skipped_failed"] == ["2026-10-28"]
    assert response.json()["series"]["skipped"] == [SAT2.isoformat()]
    assert _dates(_entries(db_session, user, TODAY, SAT3)) == [SAT1, SAT3]
    again = _put_series(client, home, unskip=[SAT2.isoformat()])
    assert again.json()["series"]["skipped"] == []


def test_preview_states(client: TestClient, db_session: Session) -> None:
    """Окно «каждую субботу»: что встанет, что пропущено, где поездка, где отмена."""
    home = _location(db_session, name="Зеленоград", is_cancelled=True, cancel_reason="Субботник")
    trip = _location(db_session, name="Кусково")
    _login(client, db_session)
    db_session.commit()
    client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT2.isoformat()})
    client.put(f"/api/planner/rest/{SAT3.isoformat()}")
    preview = client.get(
        "/api/planner/series/preview", params={"key": _key(home), "until": SAT4.isoformat(), "limit": 8}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    states = [(item["date"], item["state"], (item["other"] or {}).get("name")) for item in body["dates"]]
    assert states == [
        (SAT1.isoformat(), "cancelled", None),
        (SAT2.isoformat(), "away", "Кусково"),
        (SAT3.isoformat(), "skipped", None),
        (SAT4.isoformat(), "on", None),
    ]
    assert (body["total"], body["on"], body["starts_on"]) == (4, 1, SAT1.isoformat())
    bad = client.get("/api/planner/series/preview", params={"key": _key(home), "starts_on": "2026-10-28"})
    assert bad.status_code == 400


def test_replace_first_removes_trip_on_start_date(client: TestClient, db_session: Session) -> None:
    """К16: первая дата занята поездкой — «Заменить» убирает её в той же транзакции."""
    home = _location(db_session, name="Зеленоград")
    trip = _location(db_session, name="Кусково")
    user = _login(client, db_session)
    db_session.commit()
    client.post("/api/planner/plans", json={"key": _key(trip), "plan_date": SAT1.isoformat()})
    assert _put_series(client, home, replace_first=True).status_code == 200
    assert [entry.key for entry in _entries(db_session, user, SAT1, SAT1)] == [_key(home)]


# ---------------------------------------------------------------- что видят другие


def test_block_tabs_and_regulars_in_one_list(client: TestClient, db_session: Session) -> None:
    """Вкладки — ближайшая, даты разовых и особые; постоянные — в общем
    списке имён со значком ↻ (regular; решение Дмитрия 27.10.2026 вместо
    строки «↻ Каждую субботу: N»), порядок — по числу пробежек; total — все;
    приватные постоянные — в общем числе приватных, имени и заметок не видит
    никто."""
    park = _location(db_session, name="Кузьминки")
    _event(db_session, park, date(2025, 11, 4), 60)
    _special(db_session, HOLIDAY, ["five_verst"])
    first = _public_user(db_session, "Первая", public_slug="reg-first")
    second = _public_user(db_session, "Второй", public_slug="reg-second")
    hidden = _user(db_session, "Тайный постоянный")
    guest = _public_user(db_session, "Гость", public_slug="one-off-guest")
    for index, user in enumerate((first, second, hidden)):
        created_on = date(2026, 10, 10) + timedelta(days=index)
        _series(db_session, user, park, SAT1, created_on=created_on, is_public=user is not hidden)
    db_session.add(RunPlan(user_id=second.id, plan_date=SAT2, rest=True))
    _plan(db_session, guest, park, SAT2)
    db_session.commit()

    response = client.get("/api/planner/location", params={"key": _key(park)})
    assert "Тайный" not in response.text
    block = response.json()
    # 01.01.2027 — сид миграции 116: особые даты входят в постоянный план все,
    # без проверки прошлого года.
    new_year = date(2027, 1, 1)
    assert [day["date"] for day in block["dates"]] == [
        SAT1.isoformat(),
        SAT2.isoformat(),
        HOLIDAY.isoformat(),
        new_year.isoformat(),
    ]
    sat1, sat2, holiday, new_year_day = block["dates"]
    assert "regulars" not in sat1
    assert (new_year_day["kind"], new_year_day["total"]) == ("special", 3)
    assert (sat1["total"], sat1["private"], sat1["children"]) == (3, 1, 0)
    assert [(person["handle"], person["regular"]) for person in sat1["people"]] == [
        ("reg-first", True),
        ("reg-second", True),
    ]
    # Без привязок пробежек не посчитать — при равенстве разовые раньше.
    assert sat2["total"] == 3
    assert [(person["handle"], person["regular"]) for person in sat2["people"]] == [
        ("one-off-guest", False),
        ("reg-first", True),
    ]
    assert sat2["private"] == 1
    assert (holiday["kind"], holiday["total"]) == ("special", 3)
    assert block["my_series"] is None and block["series_hint"] is None


def test_block_tabs_not_crowded_out_by_empty_dates(db_session: Session) -> None:
    """Пустые субботы и особые даты без планов не отнимают вкладки у дат разовых:
    семь суббот с планами с декабря — шесть вкладок."""
    park = _location(db_session, name="Кузьминки")
    _special(db_session, date(2026, 12, 2), ["five_verst"], "Среда")
    _special(db_session, date(2026, 12, 9), ["five_verst"], "Ещё среда")
    guest = _public_user(db_session, "Гость", public_slug="tabs-guest")
    planned = [date(2026, 12, 5) + timedelta(weeks=week) for week in range(7)]
    for on_date in planned:
        _plan(db_session, guest, park, on_date)
    db_session.commit()
    block = location_plans_block(db_session, _key(park), None)
    assert [day["date"] for day in block["dates"]] == planned[:6]


def test_organizer_calendar_split_and_past(client: TestClient, db_session: Session) -> None:
    """Будущий день — «разово · постоянных»; прошедший — постоянные дня без
    снов и сколько из них пришли."""
    park = _location(db_session, name="Кузьминки")
    regular = _public_user(db_session, "Постоянный", public_slug="org-regular")
    guest = _public_user(db_session, "Разовый", public_slug="org-guest")
    participant = _participant(db_session, regular)
    _series(
        db_session,
        regular,
        park,
        date(2026, 9, 26),
        sleep_history=[{"from": "2026-10-04", "to": "2026-10-11"}],
    )
    for on_date in (date(2026, 10, 3), date(2026, 10, 10), PAST_SAT):
        event = _event(db_session, park, on_date, 100 + on_date.day)
        if on_date == date(2026, 10, 3):
            _run(db_session, event, participant)
    _plan(db_session, guest, park, SAT1)
    db_session.flush()

    month = run_plan_service.organizer_calendar(db_session, _identity(park), date(2026, 10, 1))
    days = {day["date"]: day for day in month["days"]}
    assert (days[date(2026, 10, 3)]["regulars"]["total"], days[date(2026, 10, 3)]["regulars_came"]) == (1, 1)
    assert days[date(2026, 10, 10)]["regulars"]["total"] == 0  # спал
    assert (days[PAST_SAT]["regulars"]["total"], days[PAST_SAT]["regulars_came"]) == (1, 0)
    assert days[PAST_SAT]["regulars"]["people"] == []
    future = days[SAT1]
    assert future["planned"] == 2
    assert [person["handle"] for person in future["people"]] == ["org-guest"]
    assert [person["handle"] for person in future["regulars"]["people"]] == ["org-regular"]


def _cache_runs(db: Session, user: User, total: int) -> None:
    db.add(
        DashboardCache(
            user_id=user.id,
            computed_at=datetime.now(UTC),
            stats={
                "total_runs": total,
                "total_volunteering": 0,
                "analytics": {"analytics_version": ANALYTICS_VERSION, "unique_locations": 1},
            },
        )
    )
    db.flush()


def test_guests_regulars_group_and_jubilee_counts_occurrences(db_session: Session) -> None:
    """«Кто у нас бежит»: постоянные — составом один раз, у дат — кто и метки
    горизонта; юбилей на второй субботе — с поправкой на первое вхождение."""
    park = _location(db_session, name="Кузьминки")
    regular = _public_user(db_session, "Девяносто восемь", public_slug="ninety-eight")
    _run(db_session, _event(db_session, park, PAST_SAT, 5), _participant(db_session, regular))
    _cache_runs(db_session, regular, 98)
    _series(db_session, regular, park, SAT1)
    db_session.flush()

    guests = planner_guests_service.organizer_guests(db_session, _identity(park), weeks=6)
    assert [person["handle"] for person in guests["regulars_people"]] == ["ninety-eight"]
    assert guests["regulars_people"][0]["marks"] == []
    by_date = {day["date"]: day["regulars"] for day in guests["dates"]}
    assert by_date[SAT1]["handles"] == ["ninety-eight"] and by_date[SAT1]["marks"] == {}
    assert by_date[SAT2]["marks"] == {"ninety-eight": [{"kind": "jubilee", "label": "100-я пробежка"}]}
    # За горизонтом трёх стартов меток нет ни у кого.
    assert by_date[SAT4]["handles"] == ["ninety-eight"] and by_date[SAT4]["marks"] == {}

    ahead = planner_guests_service.plans_ahead(db_session, [regular.id], today=TODAY, until=SAT3)[regular.id]
    assert [plan.plan_date for plan in ahead.plans] == [SAT1, SAT2, SAT3]
    assert all(plan.id.startswith("series:") for plan in ahead.plans)

    card = planner_guests_service.organizer_guest_card(db_session, _identity(park), "ninety-eight")
    assert card["series"] == {"starts_on": SAT1, "until": None}
    assert [item["date"] for item in card["planned_dates"]] == [SAT1, SAT2, SAT3]


def test_profile_line_and_next_day_show_regular(client: TestClient, db_session: Session) -> None:
    park = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    user.public_slug = "regular-profile"
    db_session.commit()
    assert _put_series(client, park).status_code == 200
    line = client.get("/api/planner/profile", params={"handle": "regular-profile"}).json()
    assert line["places"] == [{"name": "Зеленоград", "slug": park.external_key, "cancelled": False, "regular": True}]
    # Календарь «Планы» профиля — каждая суббота постоянного, «Пропущу» — дыра.
    assert client.put(f"/api/planner/rest/{SAT2.isoformat()}", json={"is_public": False}).status_code == 200
    calendar = client.get("/api/planner/profile/calendar", params={"handle": "regular-profile", "month": "2026-11"})
    november = [(day["date"], day["places"][0]["regular"]) for day in calendar.json()["days"]]
    assert november == [(day.isoformat(), True) for day in (SAT3, SAT4, date(2026, 11, 21), date(2026, 11, 28))]
    october = client.get("/api/planner/profile/calendar", params={"handle": "regular-profile", "month": "2026-10"})
    assert [day["date"] for day in october.json()["days"]] == [SAT1.isoformat()]
    nearest = client.get("/api/planner/next").json()
    assert nearest["day"]["planned"] is True and nearest["day"]["series"] is True
    assert nearest["plan"]["id"] is None and nearest["plan"]["ref"].startswith("series:")
    assert nearest["series"]["location"]["name"] == "Зеленоград"


# ---------------------------------------------------------------- уведомления


def test_reminder_for_occurrence_with_skip_link(db_session: Session) -> None:
    park = _location(db_session, name="Кузьминки", timezone="Europe/Moscow")
    user = _user(db_session, "Постоянный")
    _subscribe(db_session, user)
    _series(db_session, user, park, SAT1)
    db_session.commit()
    assert plan_notify.send_plan_reminders(db_session, now=datetime(2026, 10, 23, 16, 0, tzinfo=UTC)) == 1
    (delivery,) = _sent(db_session, user, "plan_reminders")
    text = delivery.payload["text"]
    assert text.startswith("Завтра, сб 24 октября — ")
    assert "Не получится? [Пропущу](https://run5k.test/plans#skip=2026-10-24)" in text


def test_cancelled_occurrence_reminder_has_no_skip(db_session: Session) -> None:
    """Старт отменён — «Не получится? Пропущу» не спрашиваем: старта не будет."""
    park = _location(
        db_session, name="Царицыно", timezone="Europe/Moscow", is_cancelled=True, cancel_reason="Городской праздник"
    )
    user = _user(db_session, "Постоянный")
    _subscribe(db_session, user)
    _series(db_session, user, park, SAT1)
    db_session.commit()
    assert plan_notify.send_plan_reminders(db_session, now=datetime(2026, 10, 23, 16, 0, tzinfo=UTC)) == 1
    (delivery,) = _sent(db_session, user, "plan_reminders")
    assert delivery.payload["title"] == "Завтра старт отменён"
    assert "старт отменён" in delivery.payload["text"] and "Пропущу" not in delivery.payload["text"]


def test_no_reminder_after_skip_and_trip_reminds_about_trip(db_session: Session) -> None:
    home = _location(db_session, name="Кузьминки", timezone="Europe/Moscow")
    trip = _location(db_session, name="Кусково", timezone="Europe/Moscow")
    skipper = _user(db_session, "Пропускает")
    traveller = _user(db_session, "Едет")
    for user in (skipper, traveller):
        _subscribe(db_session, user)
        _series(db_session, user, home, SAT1)
    db_session.add(RunPlan(user_id=skipper.id, plan_date=SAT1, rest=True))
    _plan(db_session, traveller, trip, SAT1)
    db_session.commit()
    assert plan_notify.send_plan_reminders(db_session, now=datetime(2026, 10, 23, 16, 0, tzinfo=UTC)) == 1
    assert _sent(db_session, skipper, "plan_reminders") == []
    (delivery,) = _sent(db_session, traveller, "plan_reminders")
    assert "Кусково" in delivery.payload["text"] and "Кузьминки" not in delivery.payload["text"]
    assert "Пропущу" not in delivery.payload["text"]


def test_cancellation_reaches_regulars_not_skipped(db_session: Session) -> None:
    park = _location(db_session, name="Кузьминки", timezone="Europe/Moscow", is_cancelled=True, cancel_reason="Забег")
    regular = _user(db_session, "Постоянный")
    skipper = _user(db_session, "Пропускает")
    for user in (regular, skipper):
        _subscribe(db_session, user)
        _series(db_session, user, park, SAT1)
    db_session.add(RunPlan(user_id=skipper.id, plan_date=SAT1, rest=True))
    db_session.commit()
    change = CancellationChange(
        platform_code="five_verst", slug=park.external_key, name=park.name, cancelled=True, reason="Забег"
    )
    assert plan_notify.notify_plan_cancellations(db_session, [change], now=datetime(2026, 10, 21, 9, 0, tzinfo=UTC)) == 1
    assert len(_sent(db_session, regular, "plan_cancellations")) == 1
    assert _sent(db_session, skipper, "plan_cancellations") == []


# ---------------------------------------------------------------- сон


def _sleep_world(db: Session, *, linked: bool = True) -> tuple[Location, User, RunPlanSeries, object]:
    """Серия с 19.09 (создана 12.09), старты дома по субботам 19.09–17.10, человек
    пробежал только 19.09."""
    park = _location(db, name="Зеленоград", started=False)
    user = _user(db, "Постоянная")
    user.public_slug = "sleepy"
    participant = _participant(db, user) if linked else _participant(db, _user(db, "Кто-то ещё"))
    for offset in range(5):
        event = _event(db, park, date(2026, 9, 19) + timedelta(weeks=offset), 200 + offset)
        if offset == 0:
            _run(db, event, participant)
    series = _series(db, user, park, date(2026, 9, 19), created_on=date(2026, 9, 12))
    _subscribe(db, user)
    return park, user, series, participant


def _sleep(db: Session, on_date: date, user: User) -> dict[str, int]:
    return plan_series_service.check_series_sleep(db, now=_msk(on_date, 10), user_ids=[user.id])


def test_sleep_after_three_missed_home_starts(db_session: Session) -> None:
    park, user, series, _participant_row = _sleep_world(db_session)
    summary = _sleep(db_session, date(2026, 10, 11), user)
    assert summary["asleep"] == 1 and summary["notified"] == 1
    db_session.refresh(series)
    assert series.sleeping_since == date(2026, 10, 11)
    assert series.sleep_missed == [date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10)]
    (message,) = _sent(db_session, user, "plan_series")
    text = message.payload["text"]
    assert "ещё ходите? Три старта подряд вас там не было" in text
    # Каждая ссылка своей строкой: в VK адрес уезжает под подпись.
    assert (
        "[Продолжить](https://run5k.test/plans#series-resume)\n[Остановить](https://run5k.test/plans#series-stop)"
        in text
    )
    # Без дат и без счёта неявок (К6).
    assert "26 сентября" not in text and "октября" not in text
    assert message.dedupe_key == f"plan-sleep:{series.id}:2026-10-11"
    # Повтор задачи сообщений не дублирует.
    assert _sleep(db_session, date(2026, 10, 12), user)["notified"] == 0
    assert len(_sent(db_session, user, "plan_series")) == 1
    # Спящий не виден: ни на странице локации, ни в напоминаниях.
    day = next((day for day in location_plans_block(db_session, _key(park), None)["dates"] if day["date"] == SAT1), None)
    assert day is None
    assert planned_starts(db_session, today=TODAY, dates=[SAT1], user_ids=[user.id]) == []


@pytest.mark.parametrize("excuse", ["rest", "trip", "cancelled", "created_late"])
def test_sleep_ignores_excused_dates(db_session: Session, excuse: str) -> None:
    """Не пропуск: «Не бегаю», поездка, отменённый старт (протокола нет), день
    создания и всё до него."""
    park, user, series, _participant_row = _sleep_world(db_session)
    missed = date(2026, 10, 3)
    if excuse == "rest":
        db_session.add(RunPlan(user_id=user.id, plan_date=missed, rest=True))
    elif excuse == "trip":
        _plan(db_session, user, _location(db_session, name="Кусково"), missed)
    elif excuse == "cancelled":
        db_session.query(Event).filter(Event.location_id == park.id, Event.event_date == missed).delete()
    else:
        series.created_at = _msk(date(2026, 10, 3), 20)
        series.starts_on = date(2026, 10, 3)
    db_session.flush()
    assert _sleep(db_session, date(2026, 10, 11), user)["asleep"] == 0
    db_session.refresh(series)
    assert series.sleeping_since is None


@pytest.mark.parametrize("variant", ["trip", "rest", "weekday_special"])
def test_presence_between_misses_is_not_sleep(db_session: Session, variant: str) -> None:
    """Был на точке в день, который пропуском не считается (поездка или «Не
    бегаю», а пробежал всё-таки дома; будний старт), — сна нет: чтение такое
    присутствие засчитывает, и задача не должна ни писать «три старта подряд
    вас не было», ни переписывать сон туда и обратно каждый день."""
    park, user, series, participant = _sleep_world(db_session)
    if variant == "weekday_special":
        _special(db_session, date(2026, 10, 7), ["five_verst"], "Будний старт")
        _run(db_session, _event(db_session, park, date(2026, 10, 7), 299), participant)
    else:
        missed = date(2026, 10, 3)
        if variant == "trip":
            _plan(db_session, user, _location(db_session, name="Кусково"), missed)
        else:
            db_session.add(RunPlan(user_id=user.id, plan_date=missed, rest=True))
        home = db_session.query(Event).filter(Event.location_id == park.id, Event.event_date == missed).one()
        _run(db_session, home, participant)
    db_session.flush()
    for day in range(18, 24):
        summary = _sleep(db_session, date(2026, 10, day), user)
        assert (summary["asleep"], summary["woke"]) == (0, 0), (day, summary)
    db_session.refresh(series)
    assert series.sleeping_since is None and series.sleep_missed is None
    assert _sent(db_session, user, "plan_series") == []
    assert not plan_series_service.series_sleeping_now(db_session, series)


def test_expired_series_does_not_fall_asleep(db_session: Session) -> None:
    """Срок V1 кончился — «Ещё ходите?» не шлём: будущих суббот нет и без сна, а
    «Продолжить» срок не продлит. Уснувший до конца срока план после него — не
    на паузе, а «Срок закончился · Продлить»."""
    _park, user, series, _participant_row = _sleep_world(db_session)
    series.until = date(2026, 10, 10)
    db_session.flush()
    assert _sleep(db_session, date(2026, 10, 11), user)["asleep"] == 0
    assert _sent(db_session, user, "plan_series") == []

    series.sleeping_since = date(2026, 10, 4)
    series.sleep_missed = [date(2026, 9, 26), date(2026, 10, 3)]
    db_session.flush()
    payload = plan_series_service.series_payload(db_session, user, series, today=TODAY)
    assert (payload["expired"], payload["sleeping"], payload["sleeping_since"]) == (True, False, None)


def test_sleep_does_not_count_today(db_session: Session) -> None:
    """Сегодняшний старт в счёт не идёт (протоколы догружаются): в субботу утром
    решение — по прошлым неделям."""
    _park, user, series, _participant_row = _sleep_world(db_session)
    # В субботу 10.10 старт 10.10 ещё не считается: три последних — 19.09
    # (был), 26.09 и 03.10.
    assert _sleep(db_session, date(2026, 10, 10), user)["asleep"] == 0
    db_session.refresh(series)
    assert series.sleeping_since is None


def test_sleep_wakes_on_presence_lazily_and_by_beat(db_session: Session) -> None:
    park, user, series, participant = _sleep_world(db_session)
    _sleep(db_session, date(2026, 10, 11), user)
    event_17 = db_session.query(Event).filter(Event.location_id == park.id, Event.event_date == PAST_SAT).one()
    _volunteer(db_session, event_17, participant)
    # Лениво: протокол пришёл — на странице локации человек снова есть.
    day = next(day for day in location_plans_block(db_session, _key(park), None)["dates"] if day["date"] == SAT1)
    assert day["total"] == 1
    # Задача записывает пробуждение: сон — в историю, счёт — после 17.10.
    assert _sleep(db_session, date(2026, 10, 18), user)["woke"] == 1
    db_session.refresh(series)
    assert series.sleeping_since is None and series.sleep_missed is None
    assert series.sleep_history == [{"from": "2026-10-11", "to": "2026-10-16"}]
    assert series.resumed_on == PAST_SAT
    # История на месте: в сне не оказалось ни одной субботы.
    past = _dates(planned_starts(db_session, today=TODAY, date_from=date(2026, 9, 19), date_to=PAST_SAT, user_ids=[user.id]))
    assert past == [date(2026, 9, 19), date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10), PAST_SAT]


def test_sleep_undone_by_late_protocol(db_session: Session) -> None:
    """К3: результат за «пропущенный» старт пришёл позже — сна не было вовсе."""
    park, user, series, participant = _sleep_world(db_session)
    _sleep(db_session, date(2026, 10, 11), user)
    late = db_session.query(Event).filter(Event.location_id == park.id, Event.event_date == date(2026, 10, 3)).one()
    _run(db_session, late, participant)
    assert _sleep(db_session, date(2026, 10, 12), user)["woke"] == 1
    db_session.refresh(series)
    assert (series.sleeping_since, series.sleep_history, series.resumed_on) == (None, [], None)


def test_resume_resets_counting(client: TestClient, db_session: Session) -> None:
    park, user, series, _participant_row = _sleep_world(db_session)
    _sleep(db_session, date(2026, 10, 11), user)
    # Входим этим же человеком: бот подтверждает по telegram_id.
    login = _login(client, db_session, telegram_id=user.telegram_id)
    assert login.id == user.id
    sleeping = client.get("/api/planner/series").json()["series"]
    assert sleeping["sleeping"] is True and sleeping["next_date"] is None
    resumed = client.post("/api/planner/series/resume")
    assert resumed.status_code == 200 and resumed.json()["series"]["sleeping"] is False
    db_session.refresh(series)
    assert series.resumed_on == TODAY
    assert series.sleep_history == [{"from": "2026-10-11", "to": "2026-10-20"}]
    # Старт 24.10 без человека — один пропуск после «Продолжить», не сон.
    _event(db_session, park, SAT1, 210)
    assert _sleep(db_session, date(2026, 10, 25), user)["asleep"] == 0
    assert client.post("/api/planner/series/resume").status_code == 200
    client.delete("/api/planner/series")
    assert client.post("/api/planner/series/resume").status_code == 404


def test_sleep_without_link_after_three_starts(db_session: Session) -> None:
    """Правило сна одно для всех (Дмитрий 26.10.2026: «3 пропуска — план
    засыпает, пробежка возобновляет, либо вручную на сайте»): без привязки к
    системам точки человека в протоколе не видно, и план засыпает через три
    старта, как бы часто он ни заходил на сайт. Сообщение так и говорит и
    зовёт привязать профиль; привязал — протокол будит, как у всех."""
    park, user, series, _other = _sleep_world(db_session, linked=False)
    user.last_seen_at = _msk(date(2026, 10, 10))
    db_session.flush()
    assert _sleep(db_session, date(2026, 10, 11), user)["asleep"] == 1
    db_session.refresh(series)
    assert series.sleep_missed == [date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10)]
    (message,) = _sent(db_session, user, "plan_series")
    text = message.payload["text"]
    assert "ещё ходите? Три старта подряд мы не видели вас в протоколах" in text
    assert "Профиль системы не привязан к сайту, и ваших пробежек нам не видно." in text
    assert text.endswith("[Привязать профиль](https://run5k.test/dashboard#profiles)")
    assert "[Продолжить](https://run5k.test/plans#series-resume)" in text
    assert "подряд вас там не было" not in text and "давно не заходили" not in text and "имя" not in text
    payload = plan_series_service.series_payload(db_session, user, series, today=TODAY)
    assert payload["sleeping"] is True and payload["wakes_by_presence"] is False

    # Привязал профиль, а в протоколе он был на одном из «пропущенных» стартов —
    # сна не было вовсе.
    participant = _participant(db_session, user)
    missed = db_session.query(Event).filter(Event.location_id == park.id, Event.event_date == date(2026, 10, 3)).one()
    _run(db_session, missed, participant)
    assert not plan_series_service.series_sleeping_now(db_session, series)
    assert _sleep(db_session, date(2026, 10, 12), user)["woke"] == 1
    db_session.refresh(series)
    assert (series.sleeping_since, series.sleep_history) == (None, [])


def test_linked_sleeping_series_wakes_by_presence(db_session: Session) -> None:
    _park, user, series, _participant_row = _sleep_world(db_session)
    _sleep(db_session, date(2026, 10, 11), user)
    db_session.refresh(series)
    payload = plan_series_service.series_payload(db_session, user, series, today=TODAY)
    assert payload["sleeping"] is True and payload["wakes_by_presence"] is True


@pytest.mark.parametrize("old_code", ["s95", "parkrun"])
def test_link_to_dead_system_does_not_keep_series_awake(db_session: Session, old_code: str) -> None:
    """Привязка только к системе, что здесь больше не бегает (parkrun-эпоха,
    старая строка S95 в узле каталога), — как без привязки: пробежек по 5 вёрст
    нам не видно, план засыпает через три старта, протокол его не будит, и окно
    не обещает «пробежите — продолжится сам» (ревью 26.10.2026, LOGIC-2/3)."""
    five = _location(db_session, name="Зеленоград", started=False)
    old = _location(db_session, old_code, name="Зеленоград старый", started=False)
    _event(db_session, old, date(2022, 2, 26), 300)
    catalog = LocationCatalog(canonical_name="Зеленоград", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    for location in (five, old):
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=location.platform_id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    for offset in range(5):
        _event(db_session, five, date(2026, 9, 19) + timedelta(weeks=offset), 200 + offset)
    user = _user(db_session, "Привязан к старой системе")
    _participant(db_session, user, old_code)
    series = _series(db_session, user, five, date(2026, 9, 19), created_on=date(2026, 9, 12))
    series.location_key = f"catalog:{catalog.id}"
    _subscribe(db_session, user)
    db_session.flush()

    identity = plan_series_service._identity_of(db_session, run_plan_service._Identities(db_session, TODAY), series)
    assert plan_series_service.wakes_by_presence(db_session, user.id, identity) is False
    preview = plan_series_service.series_preview(db_session, user, identity_key=f"catalog:{catalog.id}")
    assert preview["wakes_by_presence"] is False
    assert _sleep(db_session, date(2026, 10, 11), user)["asleep"] == 1
    db_session.refresh(series)
    assert series.sleep_missed == [date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10)]
    (message,) = _sent(db_session, user, "plan_series")
    assert "Профиль системы не привязан к сайту" in message.payload["text"]


def test_preview_says_how_new_series_wakes(db_session: Session) -> None:
    """Окно нового постоянного плана знает, как он выйдет из паузы: с привязкой
    к системе точки — протоколом, без неё — только «Продолжить»."""
    park = _location(db_session, name="Битца")
    linked = _user(db_session, "С привязкой")
    _participant(db_session, linked)
    unlinked = _user(db_session, "Без привязки")
    db_session.flush()
    assert plan_series_service.series_preview(db_session, linked, identity_key=_key(park))["wakes_by_presence"]
    assert not plan_series_service.series_preview(db_session, unlinked, identity_key=_key(park))["wakes_by_presence"]


def test_expired_series_is_stopped_after_a_month(db_session: Session) -> None:
    """К8: кончившийся срок держит слот «Продлить» месяц, потом серия
    останавливается сама, без сообщения."""
    park = _location(db_session, name="Зеленоград")
    user = _user(db_session, "Был до сентября")
    series = _series(db_session, user, park, date(2026, 8, 1), until=date(2026, 9, 5))
    summary = plan_series_service.check_series_sleep(db_session, now=_msk(date(2026, 10, 10)), user_ids=[user.id])
    assert summary["stopped"] == 1
    db_session.refresh(series)
    assert series.stopped_at is not None and series.until == date(2026, 9, 5)
    assert _sent(db_session, user, "plan_series") == []


# ---------------------------------------------------------------- остановка и подсказка


def test_stop_keeps_history_and_undo_deletes(client: TestClient, db_session: Session) -> None:
    park = _location(db_session, name="Зеленоград")
    user = _login(client, db_session)
    db_session.commit()
    assert _put_series(client, park).status_code == 200
    # «Отменить» сразу после одного нажатия: прошлого нет — строки нет.
    assert client.delete("/api/planner/series").status_code == 204
    assert db_session.query(RunPlanSeries).filter_by(user_id=user.id).count() == 0
    db_session.refresh(user)
    assert user.plans_hint_snoozed_until is not None and user.plans_hint_snoozed_until > datetime.now(UTC)

    old = _series(db_session, user, park, date(2026, 10, 3))
    db_session.commit()
    assert client.delete("/api/planner/series").status_code == 204
    db_session.refresh(old)
    assert old.stopped_at is not None and old.until == TODAY - timedelta(days=1)
    dates = [item["plan_date"] for item in _my(client, date(2026, 10, 1), SAT2)]
    assert dates == ["2026-10-03", "2026-10-10", "2026-10-17"]
    assert client.get("/api/planner/series").json()["series"] is None


def _hint_world(db: Session, ran_weeks: int) -> tuple[Location, User, object]:
    park = _location(db, name="Зеленоград", started=False)
    user = _user(db, "Почти каждую субботу")
    participant = _participant(db, user)
    for index in range(10):
        event = _event(db, park, PAST_SAT - timedelta(weeks=index), 300 - index)
        if index < ran_weeks:
            _run(db, event, participant)
    db.flush()
    return park, user, participant


def test_series_hint_threshold_and_snooze(client: TestClient, db_session: Session) -> None:
    park, user, _participant_row = _hint_world(db_session, 9)
    hint = plan_series_service.series_hint(db_session, user, today=TODAY)
    assert hint is not None and (hint["runs"], hint["of"], hint["location"]["key"]) == (9, 10, _key(park))

    login = _login(client, db_session, telegram_id=user.telegram_id)
    assert login.id == user.id
    assert client.get("/api/planner/series").json()["hint"]["runs"] == 9
    assert _block(client, park)["series_hint"]["of"] == 10
    assert client.get("/api/planner/next").json()["series_hint"]["location"]["name"] == "Зеленоград"
    assert client.post("/api/planner/series/hint/snooze").status_code == 204
    assert client.get("/api/planner/series").json()["hint"] is None


def test_series_hint_needs_six_of_ten_and_no_series(db_session: Session) -> None:
    _park, rare, _rare_participant = _hint_world(db_session, 5)
    assert plan_series_service.series_hint(db_session, rare, today=TODAY) is None
    park, often, _often_participant = _hint_world(db_session, 6)
    assert plan_series_service.series_hint(db_session, often, today=TODAY) is not None
    plan_series_service.forget_series_hint(often.id)
    _series(db_session, often, park, SAT1)
    assert plan_series_service.series_hint(db_session, often, today=TODAY) is None


def test_hint_comes_from_the_most_frequent_location(db_session: Session) -> None:
    """Подсказка — только про самую частую точку за 13 недель: дом проходит «7 из
    10», но чаще человек бывал в другом месте — там «0 из 10», и подсказки нет."""
    home, user, participant = _hint_world(db_session, 7)
    busier = _location(db_session, name="Кусково", started=False)
    for index in range(20):
        event = _event(db_session, busier, date(2026, 9, 28) + timedelta(days=index), 400 + index)
        if index < 8:
            _run(db_session, event, participant)
    db_session.flush()
    assert plan_series_service.series_hint(db_session, user, today=TODAY) is None
    # Сам по себе дом подсказку бы дал.
    _home, alone, _alone_participant = _hint_world(db_session, 7)
    hint = plan_series_service.series_hint(db_session, alone, today=TODAY)
    assert hint is not None and (hint["runs"], hint["of"]) == (7, 10) and hint["location"]["key"] != _key(home)


# ---------------------------------------------------------------- факты


def test_occurrence_facts(client: TestClient, db_session: Session) -> None:
    """Прошедшие вхождения: пробежал, был волонтёром, бегал в другом месте или
    тихий missed, который страница не красит."""
    park = _location(db_session, name="Зеленоград")
    elsewhere = _location(db_session, name="Кусково")
    user = _login(client, db_session)
    participant = _participant(db_session, user)
    ran, helped, away, missed = (date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10), PAST_SAT)
    _run(db_session, _event(db_session, park, ran, 10), participant, finish=1400)
    _volunteer(db_session, _event(db_session, park, helped, 11), participant)
    _event(db_session, park, away, 12)
    _run(db_session, _event(db_session, elsewhere, away, 77), participant, finish=1550)
    _event(db_session, park, missed, 13)
    _series(db_session, user, park, ran)
    db_session.commit()
    facts = {item["plan_date"]: item["fact"] for item in _my(client, ran, PAST_SAT)}
    assert facts[ran.isoformat()]["status"] == "ran"
    assert facts[helped.isoformat()]["status"] == "volunteered"
    assert facts[away.isoformat()] == {
        "status": "elsewhere",
        "finish_time_sec": 1550,
        "event_number": 77,
        "location": {"name": "Кусково", "slug": elsewhere.external_key},
    }
    assert facts[missed.isoformat()]["status"] == "missed"


# ---------------------------------------------------------------- удаление и объединение


def test_merge_two_people_with_series(db_session: Session) -> None:
    """Активные у обоих — у выжившего свой, поглощённый — историей; спящий
    поглощённого переезжает со сном, если у выжившего плана нет."""
    from app.services.account_merge_service import _reassign_run_plan_series

    home = _location(db_session, name="Зеленоград")
    other = _location(db_session, name="Кусково")
    survivor = _user(db_session, "Выживший")
    merged = _user(db_session, "Поглощённый")
    kept = _series(db_session, survivor, home, date(2026, 10, 3))
    moved = _series(db_session, merged, other, date(2026, 9, 26))
    fresh = _user(db_session, "Новый")
    lonely = _user(db_session, "Один")
    asleep = _series(db_session, lonely, other, date(2026, 9, 19), sleeping_since=date(2026, 10, 11))
    merged.plans_hint_snoozed_until = datetime(2026, 12, 1, tzinfo=UTC)
    db_session.flush()

    _reassign_run_plan_series(db_session, merged.id, survivor.id)
    db_session.refresh(kept)
    db_session.refresh(moved)
    assert kept.stopped_at is None and kept.user_id == survivor.id
    assert moved.user_id == survivor.id and moved.stopped_at is not None and moved.until == TODAY - timedelta(days=1)
    assert db_session.get(User, survivor.id).plans_hint_snoozed_until == datetime(2026, 12, 1, tzinfo=UTC)
    # Прошлая дата под двумя сериями — одно вхождение, серии с поздним началом.
    on_oct3 = planned_starts(db_session, today=TODAY, dates=[date(2026, 10, 3)], user_ids=[survivor.id])
    assert [entry.key for entry in on_oct3] == [_key(home)]

    _reassign_run_plan_series(db_session, lonely.id, fresh.id)
    db_session.refresh(asleep)
    assert asleep.user_id == fresh.id and asleep.stopped_at is None and asleep.sleeping_since == date(2026, 10, 11)


def test_merge_expired_survivor_keeps_live_plan(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """У выжившего срок V1 кончился (его ещё держит «Продлить»), у поглощённого
    план живой — живой и остаётся, кончившийся тихо останавливается."""
    from app.services.account_merge_service import _reassign_run_plan_series

    today = date(2026, 12, 5)
    monkeypatch.setattr("app.services.start_calendar_service.moscow_today", lambda: today)
    home = _location(db_session, name="Зеленоград")
    other = _location(db_session, name="Кузьминки")
    survivor = _user(db_session, "Выживший")
    merged = _user(db_session, "Поглощённый")
    expired = _series(db_session, survivor, home, date(2026, 10, 3), until=date(2026, 11, 28))
    live = _series(db_session, merged, other, date(2026, 10, 10))
    db_session.flush()
    _reassign_run_plan_series(db_session, merged.id, survivor.id)
    db_session.refresh(expired)
    db_session.refresh(live)
    assert expired.stopped_at is not None and expired.until == date(2026, 11, 28)
    assert live.user_id == survivor.id and live.stopped_at is None and live.until is None


def test_merge_keeps_each_plan_publicity(db_session: Session) -> None:
    """Публичность у каждой строки своя и переезжает с ней; «Не бегаю» на одну
    дату у обоих — отметка выжившего, нет её у выжившего — переезжает с
    выбором поглощённого."""
    from app.services.account_merge_service import _reassign_user_content

    home = _location(db_session, name="Зеленоград")
    survivor = _user(db_session, "Выживший")
    merged = _user(db_session, "Поглощённый")
    moved = _series(db_session, merged, home, SAT1, is_public=False)
    _plan(db_session, merged, _location(db_session, name="Кусково"), SAT2, is_public=False)
    db_session.add(RunPlan(user_id=merged.id, plan_date=SAT3, rest=True, is_public=True))
    db_session.add(RunPlan(user_id=survivor.id, plan_date=SAT4, rest=True, is_public=False))
    db_session.add(RunPlan(user_id=merged.id, plan_date=SAT4, rest=True, is_public=True))
    db_session.flush()
    _reassign_user_content(db_session, merged.id, survivor.id)
    db_session.flush()
    db_session.refresh(moved)
    assert moved.user_id == survivor.id and moved.is_public is False
    rows = {
        row.plan_date: (row.location_key is not None, row.rest, row.is_public)
        for row in db_session.query(RunPlan).filter_by(user_id=survivor.id)
    }
    assert rows == {SAT2: (True, False, False), SAT3: (False, True, True), SAT4: (False, True, False)}


def test_merged_rest_takes_survivor_occurrence(db_session: Session) -> None:
    """К17: будущее «Не бегаю» поглощённого переезжает строкой дня и снимает
    вхождение серии выжившего в этот день."""
    from app.services.account_merge_service import _reassign_user_content

    home = _location(db_session, name="Зеленоград")
    survivor = _user(db_session, "Выживший")
    merged = _user(db_session, "Поглощённый")
    _series(db_session, survivor, home, SAT1)
    _series(db_session, merged, home, SAT1)
    db_session.add(RunPlan(user_id=merged.id, plan_date=SAT2, rest=True))
    db_session.flush()
    _reassign_user_content(db_session, merged.id, survivor.id)
    assert _dates(planned_starts(db_session, today=TODAY, date_from=TODAY, date_to=SAT3, user_ids=[survivor.id])) == [
        SAT1,
        SAT3,
    ]
    assert db_session.query(RunPlanSeries).filter_by(user_id=survivor.id, stopped_at=None).count() == 1


def test_deletion_preview_counts_series(client: TestClient, db_session: Session) -> None:
    park = _location(db_session, name="Зеленоград")
    _login(client, db_session)
    db_session.commit()
    _put_series(client, park)
    preview = client.get("/api/account/deletion-preview").json()
    assert preview["run_plan_series"] == 1


def test_catalog_references_include_series() -> None:
    from app.services.location_catalog_service import CATALOG_KEY_REFERENCES

    assert ("run_plan_series", "location_key") in CATALOG_KEY_REFERENCES


def test_organizer_route_shows_regulars(client: TestClient, db_session: Session) -> None:
    """Оргкабинет: календарь и «Кто у нас бежит» отдают постоянных по API."""
    park = _location(db_session, name="Зеленоград")
    regular = _public_user(db_session, "Постоянный", public_slug="api-regular")
    _series(db_session, regular, park, SAT1)
    viewer = _login(client, db_session)
    db_session.add(LocationOrganizerAccess(user_id=viewer.id, location_key=_key(park)))
    db_session.commit()
    invalidate_organizer_locations_cache(viewer.id)
    calendar = client.get(f"/api/organizer/{park.external_key}/calendar", params={"month": "2026-10"})
    assert calendar.status_code == 200, calendar.text
    day = next(day for day in calendar.json()["days"] if day["date"] == SAT1.isoformat())
    assert day["regulars"]["total"] == 1 and day["planned"] == 1
    guests = client.get(f"/api/organizer/{park.external_key}/guests")
    assert guests.status_code == 200, guests.text
    assert [person["handle"] for person in guests.json()["regulars_people"]] == ["api-regular"]


# ---------------------------------------------------------------- сторож читателей


_PLAN_ROWS = "RunPlan.location_key.isnot(None)"
# Читать планы с локацией в обход planned_starts можно только писателям: им
# нужна сама строка дня, а не «кто планирует».
_ALLOWED_PLAN_ROW_QUERIES = {
    ("app/services/planned_starts_service.py", "*"),
    ("app/services/run_plan_service.py", "upsert_plan"),
    ("app/services/run_plan_service.py", "set_rest"),
    # Правка публичности постоянного переносит выбор на строки его точки —
    # это запись строк, а не чтение «кто планирует».
    ("app/services/plan_series_service.py", "_share_publicity"),
}
_ALLOWED_SERIES_FILES = {
    "app/models/__init__.py",
    "app/services/planned_starts_service.py",
    "app/services/plan_series_service.py",
    "app/services/account_deletion_service.py",
    "app/services/account_merge_service.py",
    # Админка «Планы»: пауза действующих — тем же sleep_states; кто планирует
    # на дату (нагрузка, план → факт) — через planned_starts.
    "app/services/admin_plans_service.py",
}


def test_plan_readers_use_planned_starts() -> None:
    """Все читатели планов — через planned_starts: читатель на голом RunPlan молча
    потерял бы постоянных (напоминание, отмена, юбилей). Свой запрос к
    RunPlanSeries — так же: без исключений и сна (К21)."""
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        if "RunPlanSeries" in source and relative not in _ALLOWED_SERIES_FILES:
            offenders.append(f"{relative}: RunPlanSeries")
        if _PLAN_ROWS not in source or (relative, "*") in _ALLOWED_PLAN_ROW_QUERIES:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            # Тело функции без вложенных: у них своя проверка.
            segment = ast.get_source_segment(source, node) or ""
            for child in ast.walk(node):
                if child is not node and isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    segment = segment.replace(ast.get_source_segment(source, child) or "", "")
            if _PLAN_ROWS in segment and (relative, node.name) not in _ALLOWED_PLAN_ROW_QUERIES:
                offenders.append(f"{relative}:{node.name}")
    assert offenders == [], f"планы читаются мимо planned_starts: {offenders}"
