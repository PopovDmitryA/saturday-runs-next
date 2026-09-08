"""Заявки на волонтёрство: участник → организатор → NRMS (без сети)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
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
    NrmsRosterSnapshot,
    Participant,
    Platform,
    PlatformLink,
    User,
    VolunteerResult,
    VolunteerSignupRequest,
)
from app.services import nrms_client
from app.services import volunteer_signup_service as svc
from app.services.location_page_service import LOCATIONS_INDEX_CACHE_KEY, resolve_location_identity

pytestmark = pytest.mark.usefixtures("_no_roster", "_no_nrms_network")


@pytest.fixture
def _no_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    """Открытую запись 5verst.ru в тестах не дёргаем — подменяем пустотой."""
    monkeypatch.setattr(svc, "fetch_volunteer_roster", lambda slug, **kwargs: None)


@pytest.fixture
def _no_nrms_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предохранитель: ни один тест не должен дойти до настоящего NRMS.

    04.09.2026 тест с непропатченным save ушёл живым POST в nrms.5verst.ru
    (с фальшивым токеном, NRMS ответил 500). Теперь любой непропатченный
    вызов падает здесь, а не в сети.
    """

    def _blocked(path: str, payload, *, token):
        raise AssertionError(f"тест дошёл до сети NRMS: {path}")

    monkeypatch.setattr(nrms_client, "_post", _blocked)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_env="test",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
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


def _login(client: TestClient, telegram_id: int, username: str) -> None:
    request_token = client.post("/api/auth/login-request").json()["request_token"]
    confirm = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": username,
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, slug: str) -> Location:
    location = Location(
        platform_id=platform.id, external_key=slug, name=f"Локация {slug}", city="Москва", country="Россия"
    )
    db.add(location)
    db.flush()
    return location


def _event(db: Session, platform: Platform, location: Location, event_date: date, number: int) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"vs-{location.external_key}-{number}",
        event_date=event_date,
        event_number=number,
        title=f"Событие {number}",
        is_test_event=False,
    )
    db.add(event)
    db.flush()
    return event


def _participant(db: Session, platform: Platform, external_id: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_id,
        display_name=name,
        profile_url=f"https://example.test/{external_id}/",
    )
    db.add(participant)
    db.flush()
    return participant


def _user(db: Session, telegram_id: int, username: str) -> User:
    user = User(telegram_id=telegram_id, telegram_username=username, telegram_chat_id=telegram_id)
    db.add(user)
    db.flush()
    return user


def _link(db: Session, user: User, platform: Platform, participant: Participant) -> None:
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )
    db.flush()


class Scene:
    """Локация 5 вёрст с организатором (волонтёрил «Организатором») и участником."""

    def __init__(self, db: Session, fake_redis: fakeredis.FakeRedis) -> None:
        suffix = str(uuid4().int % 1_000_000)
        # ID участников — 12-значные с префиксом 99: в общей дев-БД лежат
        # настоящие 9-значные verst ID (790…), и короткий суффикс с ними
        # сталкивался по уникальному ключу participants.
        ids = (f"99{uuid4().int % 10**10:010d}", f"98{uuid4().int % 10**10:010d}")
        self.platform = _platform(db, "five_verst", "5 вёрст")
        self.location = _location(db, self.platform, f"vs-loc-{suffix}")
        self.slug = self.location.external_key
        event = _event(db, self.platform, self.location, date(2026, 3, 7), 1)

        self.organizer_tg = int(uuid4().int % 10_000_000_000)
        self.organizer = _user(db, self.organizer_tg, f"org{suffix}")
        org_participant = _participant(db, self.platform, ids[0], "Сергей МИСЮКОВ")
        _link(db, self.organizer, self.platform, org_participant)
        db.add(
            VolunteerResult(
                event_id=event.id,
                participant_id=org_participant.id,
                external_result_key=f"vol-{uuid4()}",
                role="Организатор",
            )
        )

        self.runner_tg = int(uuid4().int % 10_000_000_000)
        self.runner = _user(db, self.runner_tg, f"run{suffix}")
        self.runner_participant = _participant(db, self.platform, ids[1], "Дмитрий ПОПОВ")
        _link(db, self.runner, self.platform, self.runner_participant)
        db.commit()
        fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)

    def identity(self, db: Session):
        identity = resolve_location_identity(db, self.slug)
        assert identity is not None
        return identity


def _saturday_ahead(weeks: int = 1) -> date:
    today = svc._today()
    return svc.upcoming_saturdays(today, weeks)[-1]


# ===== Служебное =====


def test_upcoming_saturdays_starts_today_if_saturday() -> None:
    saturday = date(2026, 9, 5)
    assert svc.upcoming_saturdays(saturday, 2) == [saturday, date(2026, 9, 12)]
    assert svc.upcoming_saturdays(date(2026, 9, 6), 1) == [date(2026, 9, 12)]


def test_names_match_ignores_order_and_case() -> None:
    assert svc.names_match("Дмитрий ПОПОВ", "Попов Дмитрий")
    assert svc.names_match("Артём Зубаревич", "Артем ЗУБАРЕВИЧ")
    assert not svc.names_match("Дмитрий ПОПОВ", "Дмитрий ИВАНОВ")
    assert not svc.names_match(None, "Дмитрий ПОПОВ")


def test_in_open_roster_reads_role_cell() -> None:
    request = VolunteerSignupRequest(event_date=date(2026, 9, 26), role_name="Маршал", participant_name="Дмитрий ПОПОВ")
    roster = {
        "dates": ["19.09.2026", "26.09.2026"],
        "roles": [
            {"role": "Организатор", "filled": {"26.09.2026": "Юрий РОГОВ"}},
            {"role": "Маршал", "filled": {"26.09.2026": "Попов Дмитрий"}},
        ],
    }
    assert svc.in_open_roster(request, roster) is True
    request.role_name = "Секундомер"
    assert svc.in_open_roster(request, roster) is False
    # Имя во второй строке той же роли тоже считается.
    roster["roles"].append({"role": "Секундомер", "filled": {"26.09.2026": "Кто-то ДРУГОЙ"}})
    roster["roles"].append({"role": "Секундомер", "filled": {"26.09.2026": "Дмитрий ПОПОВ"}})
    assert svc.in_open_roster(request, roster) is True
    request.role_name = "Фотограф"
    request.event_date = date(2026, 10, 31)
    assert svc.in_open_roster(request, roster) is None
    assert svc.in_open_roster(request, None) is None


# ===== Участник =====


def test_options_for_location_without_five_verst(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "runpark", "Runpark")
    location = _location(db_session, platform, f"vs-rp-{suffix}")
    _event(db_session, platform, location, date(2026, 3, 7), 1)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)
    _login(client, int(uuid4().int % 10_000_000_000), "anyone")

    response = client.get(f"/api/volunteer-signup/{location.external_key}")
    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is False
    assert body["organizer_connected"] is False


def test_options_show_link_dates_and_fallback_roles(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    scene = Scene(db_session, fake_redis)
    _login(client, scene.runner_tg, "runner")

    body = client.get(f"/api/volunteer-signup/{scene.slug}").json()
    assert body["supported"] is True
    assert body["organizer_connected"] is True
    assert body["linked"] is True
    assert body["verst_id"] == scene.runner_participant.external_user_id
    assert body["participant_name"] == "Дмитрий ПОПОВ"
    assert len(body["dates"]) == svc.FALLBACK_SATURDAYS
    assert body["roles"][0]["name"] == "Организатор"
    assert body["roster_url"].endswith(f"/{scene.slug}/volunteer/")
    assert body["my_requests"] == []


def test_options_use_open_roster_when_available(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    future = _saturday_ahead(2)
    past = date(2020, 1, 4)
    roster = {
        "source_url": "https://5verst.ru/x/volunteer/",
        "dates": [svc.format_date_ru(past), svc.format_date_ru(future)],
        "roles": [
            {"role": "Маршал", "filled": {svc.format_date_ru(future): "Иван ИВАНОВ"}},
            {"role": "Секундомер", "filled": {}},
            # Второе место той же роли — на 5verst.ru это отдельная строка.
            {"role": "Секундомер", "filled": {svc.format_date_ru(future): "Пётр ПЕТРОВ"}},
        ],
    }
    monkeypatch.setattr(svc, "fetch_volunteer_roster", lambda slug, **kwargs: roster)
    _login(client, scene.runner_tg, "runner")

    body = client.get(f"/api/volunteer-signup/{scene.slug}").json()
    assert body["roster_available"] is True
    # Прошедшая дата из записи отброшена, будущая осталась.
    assert [item["date"] for item in body["dates"]] == [future.isoformat()]
    assert [role["name"] for role in body["roles"]] == ["Маршал", "Секундомер"]
    assert body["roles"][0]["filled"] == {svc.format_date_ru(future): "Иван ИВАНОВ"}
    assert body["roles"][1]["slots"] == 2
    assert body["roles"][1]["filled"] == {svc.format_date_ru(future): "Пётр ПЕТРОВ"}


def test_create_request_and_duplicate(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    sent: list[tuple[int | None, str]] = []
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: sent.append((chat_id, text)) or True)
    _login(client, scene.runner_tg, "runner")
    event_date = _saturday_ahead(1)

    response = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": event_date.isoformat(), "role_name": "Маршал", "comment": "  могу с утра  "},
    )
    assert response.status_code == 201, response.text
    item = response.json()["item"]
    assert item["status"] == "pending"
    assert item["nrms_status"] == "none"
    assert item["comment"] == "могу с утра"
    assert item["verst_id"] == scene.runner_participant.external_user_id
    assert item["organizer_notified"] is True

    # Уведомление ушло организатору с ID участника и ссылкой на кабинет.
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == scene.organizer_tg
    assert scene.runner_participant.external_user_id in text
    assert f"/organizer/{scene.slug}/signup-requests" in text
    assert "Маршал" in text

    duplicate = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": event_date.isoformat(), "role_name": "Маршал"},
    )
    assert duplicate.status_code == 409

    body = client.get(f"/api/volunteer-signup/{scene.slug}").json()
    assert len(body["my_requests"]) == 1


def test_create_request_validations(client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    scene = Scene(db_session, fake_redis)
    _login(client, scene.runner_tg, "runner")

    past = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": "2020-01-04", "role_name": "Маршал"},
    )
    assert past.status_code == 400
    assert "прошла" in past.json()["detail"]

    far = svc._today() + timedelta(days=svc.SIGNUP_HORIZON_DAYS + 7)
    too_far = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": far.isoformat(), "role_name": "Маршал"},
    )
    assert too_far.status_code == 400


def test_create_request_requires_linked_profile(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    scene = Scene(db_session, fake_redis)
    _login(client, int(uuid4().int % 10_000_000_000), "unlinked")

    response = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": _saturday_ahead(1).isoformat(), "role_name": "Маршал"},
    )
    assert response.status_code == 400
    assert "Привяжите" in response.json()["detail"]


def test_create_request_without_organizer_is_rejected(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"vs-noorg-{suffix}")
    _event(db_session, platform, location, date(2026, 3, 7), 1)
    runner_tg = int(uuid4().int % 10_000_000_000)
    runner = _user(db_session, runner_tg, f"r{suffix}")
    _link(
        db_session,
        runner,
        platform,
        _participant(db_session, platform, f"97{uuid4().int % 10**10:010d}", "Иван ИВАНОВ"),
    )
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)
    _login(client, runner_tg, "runner")

    options = client.get(f"/api/volunteer-signup/{location.external_key}").json()
    assert options["organizer_connected"] is False
    assert "Иван ИВАНОВ" in options["fallback_message"]

    response = client.post(
        f"/api/volunteer-signup/{location.external_key}",
        json={"event_date": _saturday_ahead(1).isoformat(), "role_name": "Маршал"},
    )
    assert response.status_code == 409


def test_participant_cancels_only_pending(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    scene = Scene(db_session, fake_redis)
    _login(client, scene.runner_tg, "runner")
    created = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": _saturday_ahead(1).isoformat(), "role_name": "Секундомер"},
    ).json()["item"]

    cancelled = client.delete(f"/api/volunteer-signup/{scene.slug}/{created['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["item"]["status"] == "cancelled"

    again = client.delete(f"/api/volunteer-signup/{scene.slug}/{created['id']}")
    assert again.status_code == 409

    # После отзыва ту же заявку можно подать заново — частичный уникальный индекс не мешает.
    recreated = client.post(
        f"/api/volunteer-signup/{scene.slug}",
        json={"event_date": _saturday_ahead(1).isoformat(), "role_name": "Секундомер"},
    )
    assert recreated.status_code == 201


def test_participant_cannot_cancel_foreign_request(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    scene = Scene(db_session, fake_redis)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    _login(client, int(uuid4().int % 10_000_000_000), "stranger")
    response = client.delete(f"/api/volunteer-signup/{scene.slug}/{request.id}")
    assert response.status_code == 404


# ===== Организатор =====


def test_organizer_list_requires_access(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    scene = Scene(db_session, fake_redis)
    _login(client, scene.runner_tg, "runner")
    assert client.get(f"/api/organizer/{scene.slug}/signup-requests").status_code == 403


def test_organizer_sees_and_confirms_without_nrms_session(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    sent: list[tuple[int | None, str]] = []
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: sent.append((chat_id, text)) or True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    sent.clear()
    _login(client, scene.organizer_tg, "organizer")

    listing = client.get(f"/api/organizer/{scene.slug}/signup-requests").json()
    assert listing["pending_count"] == 1
    assert listing["items"][0]["participant_name"] == "Дмитрий ПОПОВ"
    assert listing["items"][0]["in_open_roster"] is None

    session = client.get(f"/api/organizer/{scene.slug}/nrms/session").json()
    assert session["connected"] is False

    decided = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/{request.id}/decision",
        json={"decision": "confirmed", "note": "Ждём!"},
    )
    assert decided.status_code == 200, decided.text
    item = decided.json()["item"]
    assert item["status"] == "confirmed"
    # Сессии NRMS нет — заявка подтверждена, но в NRMS её надо внести руками.
    assert item["nrms_status"] == "none"
    assert "Нет сессии NRMS" in item["nrms_error"]

    assert len(sent) == 1
    assert sent[0][0] == scene.runner_tg
    assert "вручную" in sent[0][1]
    assert "Ждём!" in sent[0][1]

    repeat = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/{request.id}/decision",
        json={"decision": "declined"},
    )
    assert repeat.status_code == 409


def test_organizer_declines(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    sent: list[tuple[int | None, str]] = []
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: sent.append((chat_id, text)) or True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    sent.clear()
    _login(client, scene.organizer_tg, "organizer")
    decided = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/{request.id}/decision",
        json={"decision": "declined", "note": "Маршалов хватает"},
    )
    assert decided.status_code == 200
    assert decided.json()["item"]["status"] == "declined"
    assert sent and "не смог принять" in sent[0][1]

    # Отклонённая заявка не блокирует новую на ту же дату и роль.
    again = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=request.event_date,
        role_name="Маршал",
        comment=None,
    )
    assert again.id != request.id


def test_confirmed_request_found_in_open_roster_marks_manual(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    event_date = _saturday_ahead(1)
    request = svc.create_signup_request(
        db_session, scene.identity(db_session), scene.runner, event_date=event_date, role_name="Маршал", comment=None
    )
    svc.decide_signup_request(
        db_session, scene.identity(db_session), scene.organizer, request.id, decision="confirmed", note=None
    )
    assert request.nrms_status == "none"

    roster = {
        "source_url": "x",
        "dates": [svc.format_date_ru(event_date)],
        "roles": [{"role": "Маршал", "filled": {svc.format_date_ru(event_date): "Попов Дмитрий"}}],
    }
    monkeypatch.setattr(svc, "fetch_volunteer_roster", lambda slug, **kwargs: roster)
    _login(client, scene.organizer_tg, "organizer")
    listing = client.get(f"/api/organizer/{scene.slug}/signup-requests").json()
    assert listing["items"][0]["in_open_roster"] is True
    db_session.refresh(request)
    assert request.nrms_status == "manual"


# ===== NRMS =====


def test_push_to_nrms_without_save_when_already_in_roster(
    db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    event_date = _saturday_ahead(1)
    request = svc.create_signup_request(
        db_session, scene.identity(db_session), scene.runner, event_date=event_date, role_name="Маршал", comment=None
    )
    verst_id = int(scene.runner_participant.external_user_id)
    session = nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1))
    nrms_client.store_session(scene.organizer.id, session)
    assert nrms_client.load_session(scene.organizer.id) == session

    calls: list[str] = []
    monkeypatch.setattr(nrms_client, "list_events", lambda s: [nrms_client.NrmsEvent(id=863, name="X", url=scene.slug)])
    monkeypatch.setattr(
        nrms_client, "list_roles", lambda s: [nrms_client.NrmsRole(id=7, name="Маршал", is_default=True)]
    )
    monkeypatch.setattr(
        nrms_client,
        "find_athlete_by_id",
        lambda s, vid: nrms_client.NrmsAthlete(vid, "Дмитрий ПОПОВ", "X", 74, True, True),
    )
    monkeypatch.setattr(
        nrms_client,
        "list_event_volunteers",
        lambda s, eid, d: nrms_client.NrmsRoster(
            status_id=2,
            upload_status_id=1,
            entries=[nrms_client.NrmsRosterEntry(verst_id, 7, "Маршал", "Дмитрий ПОПОВ")],
        ),
    )
    monkeypatch.setattr(nrms_client, "save_event_volunteers", lambda *a, **kw: calls.append("save"))

    svc.decide_signup_request(
        db_session, scene.identity(db_session), scene.organizer, request.id, decision="confirmed", note=None
    )
    assert request.nrms_status == "saved"
    assert calls == []  # уже в составе — save не нужен


def test_push_to_nrms_refuses_past_and_uploaded(
    db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    nrms_client.store_session(
        scene.organizer.id,
        nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1)),
    )
    monkeypatch.setattr(nrms_client, "list_events", lambda s: [nrms_client.NrmsEvent(id=863, name="X", url=scene.slug)])
    monkeypatch.setattr(
        nrms_client, "list_roles", lambda s: [nrms_client.NrmsRole(id=7, name="Маршал", is_default=True)]
    )
    monkeypatch.setattr(
        nrms_client, "find_athlete_by_id", lambda s, vid: nrms_client.NrmsAthlete(vid, "Д", None, 0, True, True)
    )
    monkeypatch.setattr(
        nrms_client,
        "list_event_volunteers",
        lambda s, eid, d: nrms_client.NrmsRoster(status_id=2, upload_status_id=2, entries=[]),
    )

    def _forbidden(*args, **kwargs):
        raise AssertionError("save must not be called")

    monkeypatch.setattr(nrms_client, "save_event_volunteers", _forbidden)
    svc.decide_signup_request(
        db_session, scene.identity(db_session), scene.organizer, request.id, decision="confirmed", note=None
    )
    assert request.status == "confirmed"
    assert request.nrms_status == "failed"
    assert "загружен на сайт" in request.nrms_error


def _nrms_reads(monkeypatch: pytest.MonkeyPatch, slug: str, entries: list) -> dict:
    """Подмена чтений NRMS: локация, роль «Маршал» id 7, атлет, состав из state."""
    state = {"entries": list(entries)}
    monkeypatch.setattr(nrms_client, "list_events", lambda s: [nrms_client.NrmsEvent(id=863, name="X", url=slug)])
    monkeypatch.setattr(
        nrms_client, "list_roles", lambda s: [nrms_client.NrmsRole(id=7, name="Маршал", is_default=True)]
    )
    monkeypatch.setattr(
        nrms_client,
        "find_athlete_by_id",
        lambda s, vid: nrms_client.NrmsAthlete(vid, "Д", None, 0, True, True),
    )
    monkeypatch.setattr(
        nrms_client,
        "list_event_volunteers",
        lambda s, eid, d: nrms_client.NrmsRoster(status_id=2, upload_status_id=1, entries=list(state["entries"])),
    )
    return state


def test_push_to_nrms_saves_full_roster_and_verifies(
    db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save заменяет состав даты: отдаём всех прежних плюс нового, потом перечитываем."""
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    verst_id = int(scene.runner_participant.external_user_id)
    nrms_client.store_session(
        scene.organizer.id,
        nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1)),
    )
    existing = [
        nrms_client.NrmsRosterEntry(790077966, 1, "Организатор", "Юрий РОГОВ"),
        nrms_client.NrmsRosterEntry(790084040, 6, "Связи с общественностью", "Анна СМИРНОВА"),
    ]
    state = _nrms_reads(monkeypatch, scene.slug, existing)
    saved: list[dict] = []

    def fake_save(session, *, event_id, event_date, upload_status_id, volunteers):
        saved.append({"event_id": event_id, "upload_status_id": upload_status_id, "volunteers": volunteers})
        state["entries"] = [nrms_client.NrmsRosterEntry(v, r, "", "") for v, r in volunteers]

    monkeypatch.setattr(nrms_client, "save_event_volunteers", fake_save)
    svc.decide_signup_request(
        db_session,
        scene.identity(db_session),
        scene.organizer,
        request.id,
        decision="confirmed",
        note=None,
    )
    assert request.nrms_status == "saved", request.nrms_error
    assert saved == [
        {
            "event_id": 863,
            "upload_status_id": 1,
            "volunteers": [(790077966, 1), (790084040, 6), (verst_id, 7)],
        }
    ]


def test_push_to_nrms_reports_when_verification_read_misses_someone(
    db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    nrms_client.store_session(
        scene.organizer.id,
        nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1)),
    )
    # Состав до и после записи одинаковый: NRMS «принял», но человека не добавил.
    _nrms_reads(
        monkeypatch,
        scene.slug,
        [nrms_client.NrmsRosterEntry(790077966, 1, "Организатор", "Юрий РОГОВ")],
    )
    monkeypatch.setattr(nrms_client, "save_event_volunteers", lambda *a, **kw: None)
    svc.decide_signup_request(
        db_session,
        scene.identity(db_session),
        scene.organizer,
        request.id,
        decision="confirmed",
        note=None,
    )
    assert request.nrms_status == "failed"
    assert "не хватает 1" in request.nrms_error


def test_save_event_volunteers_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    session = nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1))
    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(nrms_client, "_post", lambda path, payload, *, token: posted.append((path, payload)) or {})
    with pytest.raises(nrms_client.NrmsError):
        nrms_client.save_event_volunteers(
            session, event_id=863, event_date=date(2026, 9, 26), upload_status_id=2, volunteers=[(1, 1)]
        )
    with pytest.raises(nrms_client.NrmsError):
        nrms_client.save_event_volunteers(
            session, event_id=863, event_date=date(2026, 9, 26), upload_status_id=1, volunteers=[]
        )
    assert posted == []
    nrms_client.save_event_volunteers(
        session,
        event_id=863,
        event_date=date(2026, 9, 26),
        upload_status_id=1,
        volunteers=[(790103773, 1), (790084040, 6)],
    )
    # Ровно форма запроса из HAR Дмитрия (26.09.2026, второе сохранение).
    assert posted == [
        (
            "volunteer/event/save",
            {
                "event_id": 863,
                "date": "26.09.2026",
                "upload_status_id": 1,
                "volunteers": [
                    {"verst_id": 790103773, "role_id": 1},
                    {"verst_id": 790084040, "role_id": 6},
                ],
            },
        )
    ]


def test_auth_header_is_raw_token() -> None:
    headers = nrms_client._headers("abc.def.ghi")
    assert headers["Authorization"] == "abc.def.ghi"


def test_push_to_nrms_wrong_location_and_expired_session(
    db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    nrms_client.store_session(
        scene.organizer.id,
        nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1)),
    )
    monkeypatch.setattr(nrms_client, "list_events", lambda s: [nrms_client.NrmsEvent(id=1, name="Другая", url="other")])
    svc.decide_signup_request(
        db_session, scene.identity(db_session), scene.organizer, request.id, decision="confirmed", note=None
    )
    assert request.nrms_status == "failed"
    assert "Другая" in request.nrms_error

    # Протухший токен: сессия сбрасывается, чтобы кабинет попросил войти заново.
    second = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(2),
        role_name="Маршал",
        comment=None,
    )

    def _expired(session):
        raise nrms_client.NrmsAuthError("Сессия NRMS истекла")

    monkeypatch.setattr(nrms_client, "list_events", _expired)
    svc.decide_signup_request(
        db_session, scene.identity(db_session), scene.organizer, second.id, decision="confirmed", note=None
    )
    assert second.nrms_status == "failed"
    assert nrms_client.load_session(scene.organizer.id) is None


def test_nrms_session_store_roundtrip_and_expiry(fake_redis: fakeredis.FakeRedis) -> None:
    user_id = uuid4()
    alive = nrms_client.NrmsSession(
        token="abc.def.ghi", username="A790103773", expires_at=datetime.now(UTC) + timedelta(hours=4)
    )
    nrms_client.store_session(user_id, alive)
    stored = fake_redis.get(nrms_client.token_cache_key(user_id))
    assert stored and "abc.def.ghi" not in stored  # не открытым текстом
    assert nrms_client.load_session(user_id) == alive

    stale = nrms_client.NrmsSession(token="x", username="A1", expires_at=datetime.now(UTC) + timedelta(seconds=30))
    nrms_client.store_session(user_id, stale)
    assert nrms_client.load_session(user_id) is None  # меньше запаса — считаем протухшей
    assert fake_redis.get(nrms_client.token_cache_key(user_id)) is None


def test_jwt_expiry_is_read_from_payload() -> None:
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1788551987}).encode()).decode().rstrip("=")
    token = f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"
    assert nrms_client._jwt_expiry(token) == datetime(2026, 9, 4, 19, 59, 47, tzinfo=UTC)
    assert nrms_client._jwt_expiry("not-a-jwt") is None


def test_nrms_login_endpoint_requires_access_and_stores_session(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    _login(client, scene.runner_tg, "runner")
    assert (
        client.post(f"/api/organizer/{scene.slug}/nrms/login", json={"username": "A1", "password": "p"}).status_code
        == 403
    )

    seen: dict[str, str] = {}

    def fake_login(username: str, password: str) -> nrms_client.NrmsSession:
        seen["username"] = username
        seen["password"] = password
        return nrms_client.NrmsSession(
            token="tok", username=username, expires_at=datetime.now(UTC) + timedelta(hours=4)
        )

    monkeypatch.setattr(nrms_client, "login", fake_login)
    monkeypatch.setattr(
        nrms_client, "list_events", lambda s: [nrms_client.NrmsEvent(id=863, name="Мещерский", url="meshchersky")]
    )
    _login(client, scene.organizer_tg, "organizer")
    response = client.post(
        f"/api/organizer/{scene.slug}/nrms/login", json={"username": "A790103773", "password": "secret"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connected"] is True
    assert body["events"][0]["url"] == "meshchersky"
    assert seen == {"username": "A790103773", "password": "secret"}
    assert nrms_client.load_session(scene.organizer.id) is not None

    state = client.get(f"/api/organizer/{scene.slug}/nrms/session").json()
    assert state["connected"] is True and state["username"] == "A790103773"

    assert client.delete(f"/api/organizer/{scene.slug}/nrms/session").json()["connected"] is False
    assert nrms_client.load_session(scene.organizer.id) is None


# ===== Пакет решений и снимки состава =====


def _second_runner(db: Session, scene: Scene, name: str) -> User:
    suffix = f"96{uuid4().int % 10**10:010d}"
    user = _user(db, int(uuid4().int % 10_000_000_000), f"r{suffix}")
    _link(db, user, scene.platform, _participant(db, scene.platform, suffix, name))
    db.commit()
    return user


def test_bulk_decisions_one_save_per_date_and_snapshot(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    event_date = _saturday_ahead(1)
    identity = scene.identity(db_session)
    second = _second_runner(db_session, scene, "Анна ИВАНОВА")
    third = _second_runner(db_session, scene, "Пётр СИДОРОВ")
    r1 = svc.create_signup_request(
        db_session, identity, scene.runner, event_date=event_date, role_name="Маршал", comment=None
    )
    r2 = svc.create_signup_request(
        db_session, identity, second, event_date=event_date, role_name="Маршал", comment=None
    )
    r3 = svc.create_signup_request(db_session, identity, third, event_date=event_date, role_name="Маршал", comment=None)

    nrms_client.store_session(
        scene.organizer.id,
        nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1)),
    )
    existing = [nrms_client.NrmsRosterEntry(790077966, 1, "Организатор", "Юрий РОГОВ")]
    state = _nrms_reads(monkeypatch, scene.slug, existing)
    names = {
        int(r1.verst_id): "Дмитрий ПОПОВ",
        int(r2.verst_id): "Анна ИВАНОВА",
        int(r3.verst_id): "Пётр СИДОРОВ",
    }
    monkeypatch.setattr(
        nrms_client,
        "find_athlete_by_id",
        lambda s, vid: nrms_client.NrmsAthlete(vid, names.get(vid, "?"), None, 0, True, True),
    )
    saved: list[list[tuple[int, int]]] = []

    def fake_save(session, *, event_id, event_date, upload_status_id, volunteers):
        saved.append(list(volunteers))
        state["entries"] = [
            nrms_client.NrmsRosterEntry(v, r, "Маршал" if r == 7 else "Организатор", names.get(v, "Юрий РОГОВ"))
            for v, r in volunteers
        ]

    monkeypatch.setattr(nrms_client, "save_event_volunteers", fake_save)
    _login(client, scene.organizer_tg, "organizer")

    response = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/decisions",
        json={
            "decisions": [
                {"request_id": str(r1.id), "decision": "confirmed"},
                {"request_id": str(r3.id), "decision": "declined", "note": "хватает маршалов"},
                {"request_id": str(r2.id), "decision": "confirmed"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[str(r1.id)]["nrms_status"] == "saved"
    assert items[str(r2.id)]["nrms_status"] == "saved"
    assert items[str(r3.id)]["status"] == "declined"
    assert items[str(r3.id)]["nrms_status"] == "none"
    # Одна дата — один save с полным составом: прежний + двое новых, отклонённого нет.
    assert saved == [[(790077966, 1), (int(r1.verst_id), 7), (int(r2.verst_id), 7)]]

    # Снимок лёг на сайт тем же проходом и виден в форме записи как занятость.
    snapshots = svc.load_roster_snapshots(db_session, scene.slug, svc._today())
    assert event_date in snapshots
    assert len(snapshots[event_date].entries) == 3
    options = svc.build_signup_options(db_session, identity, third)
    label = svc.format_date_ru(event_date)
    marshal = next(role for role in options["roles"] if role["name"] == "Маршал")
    assert marshal["filled"][label] == "Дмитрий ПОПОВ, Анна ИВАНОВА"
    assert label in [d["date_display"] for d in options["dates"]]

    listing = client.get(f"/api/organizer/{scene.slug}/signup-requests").json()
    by_id = {item["id"]: item for item in listing["items"]}
    assert by_id[str(r1.id)]["in_open_roster"] is True
    assert by_id[str(r3.id)]["in_open_roster"] is False


def test_bulk_decisions_validation(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    identity = scene.identity(db_session)
    request = svc.create_signup_request(
        db_session, identity, scene.runner, event_date=_saturday_ahead(1), role_name="Маршал", comment=None
    )
    _login(client, scene.organizer_tg, "organizer")
    twice = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/decisions",
        json={
            "decisions": [
                {"request_id": str(request.id), "decision": "confirmed"},
                {"request_id": str(request.id), "decision": "declined"},
            ]
        },
    )
    assert twice.status_code == 400
    missing = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/decisions",
        json={"decisions": [{"request_id": str(uuid4()), "decision": "confirmed"}]},
    )
    assert missing.status_code == 404
    ok = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/decisions",
        json={"decisions": [{"request_id": str(request.id), "decision": "declined"}]},
    )
    assert ok.status_code == 200
    again = client.post(
        f"/api/organizer/{scene.slug}/signup-requests/decisions",
        json={"decisions": [{"request_id": str(request.id), "decision": "confirmed"}]},
    )
    assert again.status_code == 409
    db_session.refresh(request)
    assert request.status == "declined"


def test_apply_snapshots_overrides_public_roster_for_that_date() -> None:
    label = "26.09.2026"
    roster = {
        "source_url": "x",
        "dates": ["19.09.2026", label],
        "roles": [
            {"role": "Организатор", "filled": {label: "Старое ИМЯ"}},
            {"role": "Фотограф", "filled": {"19.09.2026": "Анна ФОТО"}},
        ],
    }
    snapshot = NrmsRosterSnapshot(
        five_verst_slug="x",
        event_date=date(2026, 9, 26),
        entries=[
            {"verst_id": 1, "role_id": 1, "role_name": "Организатор", "full_name": "Юрий РОГОВ"},
            {"verst_id": 2, "role_id": 7, "role_name": "Маршал", "full_name": "Дмитрий ПОПОВ"},
        ],
        fetched_at=datetime.now(UTC),
    )
    merged = svc._apply_snapshots(roster, {date(2026, 9, 26): snapshot})
    assert merged is not None
    by_role = {row["role"]: row["filled"] for row in merged["roles"]}
    assert by_role["Организатор"] == {label: "Юрий РОГОВ"}  # старое имя даты вытеснено снимком
    assert by_role["Фотограф"] == {"19.09.2026": "Анна ФОТО"}  # другая дата не тронута
    assert by_role["Маршал"] == {label: "Дмитрий ПОПОВ"}  # роли не было — добавлена
    assert merged["dates"] == ["19.09.2026", label]
    # Без открытой записи снимок даёт и даты, и роли.
    alone = svc._apply_snapshots(None, {date(2026, 9, 26): snapshot})
    assert alone["dates"] == [label]
    assert {row["role"] for row in alone["roles"]} == {"Организатор", "Маршал"}


def test_push_to_nrms_refuses_cancelled_event(
    db_session: Session, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(db_session, fake_redis)
    monkeypatch.setattr(svc, "send_user_telegram_message", lambda chat_id, text: True)
    request = svc.create_signup_request(
        db_session,
        scene.identity(db_session),
        scene.runner,
        event_date=_saturday_ahead(1),
        role_name="Маршал",
        comment=None,
    )
    nrms_client.store_session(
        scene.organizer.id,
        nrms_client.NrmsSession(token="t", username="A1", expires_at=datetime.now(UTC) + timedelta(hours=1)),
    )
    _nrms_reads(monkeypatch, scene.slug, [])
    monkeypatch.setattr(
        nrms_client,
        "list_event_volunteers",
        lambda s, eid, d: nrms_client.NrmsRoster(status_id=3, upload_status_id=1, entries=[]),
    )

    def _forbidden(*args, **kwargs):
        raise AssertionError("save must not be called")

    monkeypatch.setattr(nrms_client, "save_event_volunteers", _forbidden)
    svc.decide_signup_request(
        db_session, scene.identity(db_session), scene.organizer, request.id, decision="confirmed", note=None
    )
    assert request.nrms_status == "failed"
    assert "отменён" in request.nrms_error
