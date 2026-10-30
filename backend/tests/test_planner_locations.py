"""«Где бежите?» — окно плана от даты (GET /planner/locations, решение Дмитрия
26.10.2026): на дату — только локации, которые можно запланировать по тем же
правилам, что календарь локации; подсказки без запроса и поиск с теми же
сокращениями мест, что поиск по сайту."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, date, datetime

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import Location, RunPlan, StartSpecialDate
from app.services import home_location_brief_service, location_page_service, planner_locations_service
from tests.test_plan_series import _series
from tests.test_run_plans import (
    ADMIN_TELEGRAM_ID,
    HOLIDAY,
    SAT1,
    SAT2,
    TODAY,
    _event,
    _key,
    _location,
    _login,
    _participant,
    _plan,
    _run,
)

TUESDAY = date(2026, 10, 27)
SAT3 = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "app.services.start_calendar_service.moscow_today",
        "app.services.run_plan_service.moscow_today",
        "app.services.plan_series_service.moscow_today",
        "app.services.planner_locations_service.moscow_today",
        "app.api.routes.planner.moscow_today",
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


@pytest.fixture(autouse=True)
def _fresh_memo() -> Generator[None, None, None]:
    home_location_brief_service.reset_home_location_memo()
    yield
    home_location_brief_service.reset_home_location_memo()


def _index(redis: fakeredis.FakeRedis, *rows: tuple[Location, str, dict[str, object]]) -> None:
    """Каталог локаций в Redis — из него ищет поиск по сайту и «Где бежите?»."""
    items = []
    for location, code, extra in rows:
        items.append(
            {
                "slug": location.external_key,
                "identity_key": _key(location),
                "name": location.name,
                "city": location.city,
                "region": extra.pop("region", None),
                "country": "Россия",
                "platform_codes": [code],
                "is_paused": False,
                "is_cancelled": False,
                "is_series": False,
                "events_count": extra.pop("events", 10),
                "finishers_total": 100,
                **extra,
            }
        )
    redis.set(location_page_service.LOCATIONS_INDEX_CACHE_KEY, json.dumps({"items": items, "series": []}))


def _place(db: Session, name: str, city: str) -> Location:
    location = _location(db, name=name)
    location.city = city
    db.flush()
    return location


def _locations(client: TestClient, on_date: date, q: str | None = None) -> dict[str, object]:
    params = {"date": on_date.isoformat(), **({"q": q} if q is not None else {})}
    response = client.get("/api/planner/locations", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def _names(items: object) -> list[str]:
    return [item["name"] for item in items]  # type: ignore[attr-defined, index]


def test_login_required(client: TestClient) -> None:
    assert client.get("/api/planner/locations", params={"date": SAT1.isoformat()}).status_code == 401


def test_only_start_days_in_horizon(client: TestClient, db_session: Session) -> None:
    """Дата — день старта от сегодня на год вперёд; иначе 400 с понятным текстом."""
    _login(client, db_session)
    for on_date, text in (
        (TUESDAY, "В этот день старта нет"),
        (date(2026, 10, 17), "Этот старт уже прошёл"),
        (date(2027, 11, 6), "В этот день старта нет"),
    ):
        response = client.get("/api/planner/locations", params={"date": on_date.isoformat()})
        assert (response.status_code, response.json()["detail"]) == (400, text), on_date


def test_search_keeps_only_plannable_on_date(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    """Те же правила, что календарь локации: пауза, «ещё не открылась»,
    parkrun и серия — мимо; на будний особый день — только системы этой даты,
    особая суббота чужой системы — обычная суббота для всех."""
    five = _location(db_session, name="Кузьминки")
    s95 = _location(db_session, "s95", name="Коломенское")
    paused = _location(db_session, name="Кусково", is_paused=True)
    unopened = _location(db_session, name="Красногорск", started=False)
    parkrun = _location(db_session, "parkrun", name="Котельники")
    series = _location(db_session, name="Каравай", is_series=True)
    _index(
        fake_redis,
        (five, "five_verst", {"events": 50}),
        (s95, "s95", {"events": 40}),
        # Каталог считает «Кусково» живым, а строка на паузе — точная проверка отсекает.
        (paused, "five_verst", {"events": 30}),
        (unopened, "five_verst", {"events": 0}),
        (parkrun, "parkrun", {"events": 20}),
        (series, "five_verst", {"events": 10, "is_series": True}),
    )
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["five_verst"]))
    db_session.add(StartSpecialDate(start_date=SAT2, title="Особая суббота S95", platforms=["s95"]))
    _event(db_session, five, date(2026, 10, 10), 120)
    _login(client, db_session)

    saturday = _locations(client, SAT1, "москва")
    assert _names(saturday["results"]) == ["Кузьминки", "Коломенское"]
    assert saturday["suggested"] == []
    first = saturday["results"][0]
    assert first == {
        "key": _key(five),
        "slug": five.external_key,
        "name": "Кузьминки",
        "city": "Москва",
        "platforms": ["five_verst"],
        "number_forecast": 122,
        "cancelled": False,
        "mine": False,
        "regular": False,
        "upcoming": False,
    }
    assert (saturday["kind"], saturday["title"], saturday["limit"], saturday["rest"]) == ("regular", None, 1, False)

    holiday = _locations(client, HOLIDAY, "москва")
    assert _names(holiday["results"]) == ["Кузьминки"]
    assert (holiday["kind"], holiday["title"], holiday["limit"]) == ("special", "День народного единства", 3)

    # Особая суббота S95 — у 5 вёрст обычная суббота.
    assert _names(_locations(client, SAT2, "москва")["results"]) == ["Кузьминки", "Коломенское"]


def test_search_place_aliases_and_yo(client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """Сокращения мест, регион целиком и «е» = «ё» — как в поиске по сайту."""
    piter = _place(db_session, "Елагин остров", "Санкт-Петербург")
    birch = _place(db_session, "Берёзовая роща", "Новосибирск")
    zhuk = _place(db_session, "Жуковский", "Жуковский")
    _index(
        fake_redis,
        (piter, "five_verst", {"region": "Санкт-Петербург"}),
        (birch, "five_verst", {"region": "Новосибирская"}),
        (zhuk, "five_verst", {"region": "Московская"}),
    )
    _login(client, db_session)
    assert _names(_locations(client, SAT1, "спб")["results"]) == ["Елагин остров"]
    assert _names(_locations(client, SAT1, "питер")["results"]) == ["Елагин остров"]
    assert _names(_locations(client, SAT1, "березовая")["results"]) == ["Берёзовая роща"]
    assert _names(_locations(client, SAT1, "нск")["results"]) == ["Берёзовая роща"]
    assert _names(_locations(client, SAT1, "подмосковье")["results"]) == ["Жуковский"]
    # Один знак — не поиск: подсказки.
    short = _locations(client, SAT1, "ж")
    assert short["results"] == []


def test_suggested_order_and_flags(client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """Подсказки: постоянная, домашняя, точки планов за 90 дней (сначала
    недавние), частые по пробежкам за год; mine — план сюда на эту дату (в том
    числе вхождение постоянного), regular — точка постоянного. Непланируемая
    домашняя — мимо."""
    regular = _location(db_session, name="Зеленоград")
    home = _location(db_session, name="Мещерский")
    trip = _location(db_session, name="Битца")
    older_trip = _location(db_session, name="Царицыно")
    frequent = _location(db_session, name="Измайлово")
    paused_home = _location(db_session, name="Кусково", is_paused=True)
    _index(fake_redis, *((row, "five_verst", {}) for row in (regular, home, trip, older_trip, frequent)))
    user = _login(client, db_session)
    user.home_location_key = _key(home)
    _series(db_session, user, regular, SAT1)
    _plan(db_session, user, older_trip, date(2026, 11, 14), created_at=datetime(2026, 10, 1, tzinfo=UTC))
    _plan(db_session, user, trip, SAT2, created_at=datetime(2026, 10, 20, tzinfo=UTC))
    participant = _participant(db_session, user)
    for week, on_date in enumerate((date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 10))):
        _run(db_session, _event(db_session, frequent, on_date, 300 + week), participant)
    db_session.commit()

    sat1 = _locations(client, SAT1)
    assert _names(sat1["suggested"]) == ["Зеленоград", "Мещерский", "Битца", "Царицыно", "Измайлово"]
    assert sat1["results"] == []
    flags = {item["name"]: (item["mine"], item["regular"]) for item in sat1["suggested"]}
    assert flags["Зеленоград"] == (True, True)
    assert flags["Битца"] == (False, False)
    # Поездка 31.10 вытеснила субботу постоянного: «моё» на эту дату — Битца.
    sat2 = {item["name"]: (item["mine"], item["regular"]) for item in _locations(client, SAT2)["suggested"]}
    assert sat2["Битца"] == (True, False) and sat2["Зеленоград"] == (False, True)

    # Домашняя на паузе — мимо подсказок.
    user.home_location_key = _key(paused_home)
    db_session.commit()
    assert "Кусково" not in _names(_locations(client, SAT1)["suggested"])


def test_rest_and_cancelled_start(client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    """«Не бегаю» на день — в ответе; отменённый старт в списке, с пометкой."""
    cancelled = _location(db_session, name="Сокольники", is_cancelled=True, cancel_reason="Субботник")
    _index(fake_redis, (cancelled, "five_verst", {"is_cancelled": True}))
    user = _login(client, db_session)
    db_session.add(RunPlan(user_id=user.id, plan_date=SAT1, rest=True))
    db_session.commit()
    payload = _locations(client, SAT1, "сокольники")
    assert payload["rest"] is True
    (item,) = payload["results"]
    assert (item["name"], item["cancelled"]) == ("Сокольники", True)
    assert _locations(client, SAT2, "сокольники")["results"][0]["cancelled"] is False


def test_saved_from_date_window_like_from_location(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    """Сохранение — общий POST /planner/plans: турист расписывает субботы
    подряд, и каждая сохранённая точка становится подсказкой следующей даты."""
    first = _location(db_session, name="Битца")
    second = _location(db_session, name="Кусково")
    _index(fake_redis, (first, "five_verst", {}), (second, "five_verst", {}))
    _login(client, db_session)
    assert _locations(client, SAT1)["suggested"] == []
    for hour, (on_date, location) in enumerate(((SAT1, first), (SAT2, second))):
        (option,) = _locations(client, on_date, location.name)["results"]
        saved = client.post("/api/planner/plans", json={"key": option["key"], "plan_date": on_date.isoformat()})
        assert saved.status_code == 200, saved.text
        # now() у всех запросов теста один (внешняя транзакция) — порядок задаём сами.
        plan = db_session.get(RunPlan, saved.json()["plan"]["id"])
        plan.created_at = datetime(2026, 10, 21, 10 + hour, tzinfo=UTC)
        db_session.commit()
    # Сначала недавно отмеченные.
    assert _names(_locations(client, SAT3)["suggested"]) == ["Кусково", "Битца"]
    assert _locations(client, SAT1)["suggested"][1]["mine"] is True


def test_cut_search_is_marked_truncated(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Поиск городом упирается в лимит молча — окно должно попросить уточнить
    запрос (в Москве планируемых локаций вдвое больше лимита; ревью 26.10.2026,
    LOGIC-4)."""
    monkeypatch.setattr(planner_locations_service, "RESULTS_LIMIT", 2)
    parks = [_place(db_session, name, "Тверь") for name in ("Бобачевская роща", "Затверечье", "Комсомольская роща")]
    _index(fake_redis, *((park, "five_verst", {"events": 30 - index}) for index, park in enumerate(parks)))
    _login(client, db_session)
    cut = _locations(client, SAT1, "тверь")
    assert (len(cut["results"]), cut["truncated"]) == (2, True)
    exact = _locations(client, SAT1, "затверечье")
    assert (_names(exact["results"]), exact["truncated"]) == (["Затверечье"], False)
    assert _locations(client, SAT1)["truncated"] is False


def test_day_limit_known_before_save(client: TestClient, db_session: Session) -> None:
    """Лимит дня окно знает заранее: от даты — разовые планы дня (planned), от
    локации — даты с планами в других локациях (elsewhere, full — день
    заполнен). Иначе о лимите человек узнавал по 409 после «Планирую» (ревью
    26.10.2026, LOGIC-5 / UI-3)."""
    bitsa = _location(db_session, name="Битца")
    kuskovo = _location(db_session, name="Кусково")
    holiday = _location(db_session, name="Царицыно")
    db_session.add(StartSpecialDate(start_date=HOLIDAY, title="День народного единства", platforms=["five_verst"]))
    user = _login(client, db_session)
    _plan(db_session, user, bitsa, SAT1)
    _plan(db_session, user, holiday, HOLIDAY)
    db_session.commit()

    day = _locations(client, SAT1)
    assert ([item["name"] for item in day["planned"]], day["limit"]) == (["Битца"], 1)
    assert _locations(client, SAT2)["planned"] == []
    assert client.post(
        "/api/planner/plans", json={"key": _key(kuskovo), "plan_date": SAT1.isoformat()}
    ).status_code == 409

    block = client.get("/api/planner/location", params={"key": _key(kuskovo)}).json()
    elsewhere = [(item["date"], [loc["name"] for loc in item["locations"]], item["full"]) for item in block["elsewhere"]]
    assert elsewhere == [(SAT1.isoformat(), ["Битца"], True), (HOLIDAY.isoformat(), ["Царицыно"], False)]
    # Свои планы этой локации — не «в другой».
    own = client.get("/api/planner/location", params={"key": _key(bitsa)}).json()
    assert [item["date"] for item in own["elsewhere"]] == [HOLIDAY.isoformat()]
    # Гостю — пусто.
    client.cookies.clear()
    assert client.get("/api/planner/location", params={"key": _key(kuskovo)}).json()["elsewhere"] == []
