from __future__ import annotations

from collections.abc import Generator
from datetime import date
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
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(db_session: Session, fake_redis: fakeredis.FakeRedis, auth_settings: Settings) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: auth_settings

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _authenticated_client(client: TestClient) -> TestClient:
    telegram_id = int(uuid4().int % 10_000_000_000)
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"home_loc_tester_{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200, confirm_response.text
    token = confirm_response.json()["magic_link"].split("token=")[1]
    callback_response = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    assert callback_response.status_code == 302
    return client


_PARTICIPANT_CACHE: dict[tuple[str, str], Participant] = {}


def _ensure_participant_link(db_session: Session, user: User, platform_code: str) -> Participant:
    """One participant/platform-link per (user, platform) — the DB allows only one."""
    cache_key = (str(user.id), platform_code)
    cached = _PARTICIPANT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Home Location Tester",
        profile_url=f"https://example.test/{external_user_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_user_id,
            external_url=participant.profile_url,
        )
    )
    db_session.flush()
    _PARTICIPANT_CACHE[cache_key] = participant
    return participant


def _seed_run(
    db_session: Session,
    *,
    user: User,
    platform_code: str,
    location_slug: str,
    location_name: str,
    city: str,
    event_date: date,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    location = (
        db_session.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key == location_slug)
        .one_or_none()
    )
    if location is None:
        location = Location(
            platform_id=platform.id,
            external_key=location_slug,
            name=location_name,
            city=city,
            country="Россия",
        )
        db_session.add(location)
        db_session.flush()

    participant = _ensure_participant_link(db_session, user, platform_code)
    external_user_id = participant.external_user_id

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"test:{location_slug}:{external_user_id}:{event_date.isoformat()}",
        event_date=event_date,
        event_number=1,
        title=f"Test event at {location_name}",
    )
    db_session.add(event)
    db_session.flush()

    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"test:{location_slug}:{external_user_id}:{event_date.isoformat()}",
            position=1,
            finish_time_sec=25 * 60,
            finish_time_display="00:25:00",
            status="finished",
        )
    )
    db_session.commit()


def test_home_location_defaults_to_most_run_location(
    client: TestClient, db_session: Session
) -> None:
    auth_client = _authenticated_client(client)
    me = auth_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    test_slug = f"home-loc-test-{uuid4().hex[:8]}"
    _seed_run(
        db_session,
        user=user,
        platform_code="five_verst",
        location_slug=f"rare-{test_slug}",
        location_name="Редкий парк",
        city="Тестоград",
        event_date=date(2099, 1, 4),
    )
    for offset in range(3):
        _seed_run(
            db_session,
            user=user,
            platform_code="five_verst",
            location_slug=f"frequent-{test_slug}",
            location_name="Частый парк",
            city="Тестоград",
            event_date=date(2099, 1, 11 + offset * 7),
        )

    response = auth_client.get("/api/settings/home-location")
    assert response.status_code == 200
    data = response.json()
    assert data["is_auto"] is True
    assert data["location"]["name"] == "Частый парк"
    assert data["location"]["run_count"] == 3


def test_home_location_dedupes_catalog_linked_platforms(
    client: TestClient, db_session: Session
) -> None:
    auth_client = _authenticated_client(client)
    me = auth_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    test_slug = f"home-loc-dedupe-{uuid4().hex[:8]}"
    catalog = LocationCatalog(canonical_name="Единый парк")
    db_session.add(catalog)
    db_session.flush()

    _seed_run(
        db_session,
        user=user,
        platform_code="five_verst",
        location_slug=f"fv-{test_slug}",
        location_name="Единый парк (5 вёрст)",
        city="Тестоград",
        event_date=date(2099, 2, 7),
    )
    _seed_run(
        db_session,
        user=user,
        platform_code="runpark",
        location_slug=f"rp-{test_slug}",
        location_name="Единый парк (RunPark)",
        city="Тестоград",
        event_date=date(2099, 2, 14),
    )

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    runpark = db_session.query(Platform).filter(Platform.code == "runpark").one()
    db_session.add_all(
        [
            LocationCatalogLink(catalog_id=catalog.id, platform_id=five_verst.id, external_key=f"fv-{test_slug}"),
            LocationCatalogLink(catalog_id=catalog.id, platform_id=runpark.id, external_key=f"rp-{test_slug}"),
        ]
    )
    db_session.commit()

    candidates_response = auth_client.get("/api/settings/home-location/candidates")
    assert candidates_response.status_code == 200
    matching = [c for c in candidates_response.json() if c["catalog_identity_key"] == f"catalog:{catalog.id}"]
    assert len(matching) == 1
    assert matching[0]["run_count"] == 2
    assert sorted(matching[0]["platform_codes"]) == ["five_verst", "runpark"]


def test_home_location_manual_override_and_reset(
    client: TestClient, db_session: Session
) -> None:
    auth_client = _authenticated_client(client)
    me = auth_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    test_slug = f"home-loc-manual-{uuid4().hex[:8]}"
    _seed_run(
        db_session,
        user=user,
        platform_code="five_verst",
        location_slug=f"a-{test_slug}",
        location_name="Локация А",
        city="Тестоград",
        event_date=date(2099, 3, 7),
    )
    for offset in range(2):
        _seed_run(
            db_session,
            user=user,
            platform_code="five_verst",
            location_slug=f"b-{test_slug}",
            location_name="Локация Б",
            city="Тестоград",
            event_date=date(2099, 3, 14 + offset * 7),
        )

    candidates = auth_client.get("/api/settings/home-location/candidates").json()
    location_a = next(c for c in candidates if c["name"] == "Локация А")

    override_response = auth_client.put(
        "/api/settings/home-location",
        json={"catalog_identity_key": location_a["catalog_identity_key"]},
    )
    assert override_response.status_code == 200
    assert override_response.json()["is_auto"] is False
    assert override_response.json()["location"]["name"] == "Локация А"

    get_response = auth_client.get("/api/settings/home-location")
    assert get_response.json()["location"]["name"] == "Локация А"
    assert get_response.json()["is_auto"] is False

    reset_response = auth_client.put("/api/settings/home-location", json={"catalog_identity_key": None})
    assert reset_response.status_code == 200
    assert reset_response.json()["is_auto"] is True
    assert reset_response.json()["location"]["name"] == "Локация Б"


def test_home_location_change_is_timestamped(client: TestClient, db_session: Session) -> None:
    """Отметка времени смены дома — на ней держится плашка «рейтинг пересчитается».

    Ставится только при реальной смене: повторное сохранение той же площадки не
    должно заново обещать пересчёт таблицы.
    """
    auth_client = _authenticated_client(client)
    me = auth_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    assert user.home_location_changed_at is None

    test_slug = f"home-loc-stamp-{uuid4().hex[:8]}"
    _seed_run(
        db_session,
        user=user,
        platform_code="five_verst",
        location_slug=f"a-{test_slug}",
        location_name="Локация А",
        city="Тестоград",
        event_date=date(2099, 4, 4),
    )
    _seed_run(
        db_session,
        user=user,
        platform_code="five_verst",
        location_slug=f"b-{test_slug}",
        location_name="Локация Б",
        city="Тестоград",
        event_date=date(2099, 4, 11),
    )
    candidates = auth_client.get("/api/settings/home-location/candidates").json()
    location_a = next(c for c in candidates if c["name"] == "Локация А")

    assert (
        auth_client.put(
            "/api/settings/home-location",
            json={"catalog_identity_key": location_a["catalog_identity_key"]},
        ).status_code
        == 200
    )
    db_session.refresh(user)
    stamped_at = user.home_location_changed_at
    assert stamped_at is not None

    # Та же площадка ещё раз — отметка не двигается.
    assert (
        auth_client.put(
            "/api/settings/home-location",
            json={"catalog_identity_key": location_a["catalog_identity_key"]},
        ).status_code
        == 200
    )
    db_session.refresh(user)
    assert user.home_location_changed_at == stamped_at

    # Сброс на авто — тоже смена дома.
    assert (
        auth_client.put(
            "/api/settings/home-location", json={"catalog_identity_key": None}
        ).status_code
        == 200
    )
    db_session.refresh(user)
    assert user.home_location_changed_at is not None
    assert user.home_location_changed_at > stamped_at


def test_home_location_rejects_unknown_key(client: TestClient) -> None:
    auth_client = _authenticated_client(client)
    response = auth_client.put(
        "/api/settings/home-location",
        json={"catalog_identity_key": "catalog:00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 400


def test_home_location_requires_auth(client: TestClient) -> None:
    assert client.get("/api/settings/home-location").status_code == 401
    assert client.get("/api/settings/home-location/candidates").status_code == 401
    assert client.put("/api/settings/home-location", json={"catalog_identity_key": None}).status_code == 401
