"""Дальность стартов от домашней локации: расчёт, плитка, модалка, рейтинг."""
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
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.home_distance_service import (
    AMBIGUITY_RATIO,
    _home_ambiguity,
    haversine_km,
    round_km,
)
from app.services.home_location_service import HomeLocationCandidate
from app.services.leaderboard_service import (
    _home_location_note,
    _LocationVisits,
    _russian_identities,
)
from app.services.location_catalog_service import LocationCatalogIndex

# Опорные точки: Москва — «дом», Хабаровск — самый дальний старт (~6140 км по
# прямой), соседний парк — проверка десятых долей на близких расстояниях.
MOSCOW = (55.7500, 37.6200)
KHABAROVSK = (48.4800, 135.0700)
NEARBY = (55.8000, 37.7000)


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


def _authenticated_client(client: TestClient) -> TestClient:
    telegram_id = int(uuid4().int % 10_000_000_000)
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"home_dist_tester_{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200, confirm_response.text
    token = confirm_response.json()["magic_link"].split("token=")[1]
    callback_response = client.get(
        "/api/auth/callback", params={"token": token}, follow_redirects=False
    )
    assert callback_response.status_code == 302
    return client


def _current_user(client: TestClient, db_session: Session) -> User:
    me = client.get("/api/auth/me")
    return db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()


def _participant(db_session: Session, user: User, platform_code: str) -> Participant:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    existing = (
        db_session.query(Participant)
        .join(PlatformLink, PlatformLink.participant_id == Participant.id)
        .filter(PlatformLink.user_id == user.id, PlatformLink.platform_id == platform.id)
        .one_or_none()
    )
    if existing is not None:
        return existing
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Home Distance Tester",
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
    return participant


def _location(
    db_session: Session,
    slug: str,
    name: str,
    coordinates: tuple[float, float],
    *,
    platform_code: str = "five_verst",
    official_map: bool = True,
) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    location = (
        db_session.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key == slug)
        .one_or_none()
    )
    if location is None:
        location = Location(
            platform_id=platform.id,
            external_key=slug,
            name=name,
            city=name,
            country="Россия",
            latitude=coordinates[0],
            longitude=coordinates[1],
            is_official_map=official_map,
        )
        db_session.add(location)
        db_session.flush()
    return location


def _seed_runs(
    db_session: Session,
    user: User,
    location: Location,
    dates: list[date],
    *,
    platform_code: str = "five_verst",
) -> None:
    participant = _participant(db_session, user, platform_code)
    platform = db_session.query(Platform).filter(Platform.code == platform_code).one()
    for event_date in dates:
        key = f"hd:{location.external_key}:{participant.external_user_id}:{event_date.isoformat()}"
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=key,
            event_date=event_date,
            event_number=1,
            title=f"Test event at {location.name}",
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=key,
                position=1,
                finish_time_sec=25 * 60,
                finish_time_display="00:25:00",
                status="finished",
            )
        )
    db_session.commit()


def test_haversine_matches_known_distance() -> None:
    """Москва — Хабаровск по прямой ≈ 6140 км (справочное значение)."""
    assert haversine_km(MOSCOW, KHABAROVSK) == pytest.approx(6140, abs=25)
    assert haversine_km(MOSCOW, MOSCOW) == 0


def test_round_km_keeps_tenths_for_short_hops() -> None:
    """Близкие площадки — с десятыми: «0,8 км» честнее округления до «1»."""
    assert round_km(0.83) == 0.8
    assert round_km(9.44) == 9.4
    assert round_km(10.4) == 10.0
    assert round_km(6140.7) == 6141.0


def _candidate(key: str, name: str, run_count: int) -> HomeLocationCandidate:
    return HomeLocationCandidate(
        catalog_identity_key=key,
        name=name,
        city=None,
        region=None,
        run_count=run_count,
        volunteer_count=0,
        platform_codes=["five_verst"],
    )


def test_home_ambiguity_flags_tie_and_close_runner_up() -> None:
    leader = _candidate("a", "Альфа", 10)
    tie = _candidate("b", "Бета", 10)
    close = _candidate("c", "Гамма", int(10 * AMBIGUITY_RATIO) + 1)
    far = _candidate("d", "Дельта", 2)

    assert _home_ambiguity([leader, tie], leader, is_auto=True) == ("tie", "Бета")
    assert _home_ambiguity([leader, close], leader, is_auto=True) == ("close", "Гамма")
    assert _home_ambiguity([leader, far], leader, is_auto=True) == (None, None)
    # Выбранное руками не комментируем: человек уже сказал, где его дом.
    assert _home_ambiguity([leader, tie], leader, is_auto=False) == (None, None)


def _visits(count: int) -> _LocationVisits:
    return _LocationVisits(first_date=date(2099, 1, 3), codes={"five_verst"}, visits=count)


def test_rating_marks_shaky_auto_home_but_not_a_clear_leader() -> None:
    names = {"a": "Альфа", "b": "Бета"}
    close = {"a": _visits(10), "b": _visits(8)}
    clear = {"a": _visits(10), "b": _visits(2)}
    alone = {"a": _visits(10)}

    assert _home_location_note(close, names, "a", None) == "ambiguous"
    assert _home_location_note(clear, names, "a", None) is None
    assert _home_location_note(alone, names, "a", None) is None


def test_rating_marks_manual_home_outside_the_top_three() -> None:
    names = {chr(ord("a") + i): f"Площадка {i}" for i in range(5)}
    identities = {key: _visits(10 - index) for index, key in enumerate(names)}

    # Руками выбрана пятая по числу визитов площадка — это и помечаем.
    assert _home_location_note(identities, names, "e", "e") == "manual_off_top"
    # Внутри тройки ручной выбор не комментируем, даже если площадки вровень.
    assert _home_location_note(identities, names, "b", "b") is None
    assert _home_location_note(identities, names, "c", "c") is None


def test_rating_counts_only_russian_home_locations(db_session: Session) -> None:
    """Живущие за границей в рейтинг не идут: их нулевая точка на другом
    континенте, и любой старт в России давал бы десятки тысяч километров."""
    suffix = uuid4().hex[:8]
    home = _location(db_session, f"hd-ru-{suffix}", "Русский парк", MOSCOW)
    abroad = _location(
        db_session, f"hd-abroad-{suffix}", "Заграничный парк", KHABAROVSK, platform_code="s95"
    )
    abroad.country = "Serbia"
    db_session.commit()

    russian = _russian_identities(db_session)
    catalog_index = LocationCatalogIndex(db_session)
    assert catalog_index.canonical_identity_key(home, "five_verst") in russian
    assert catalog_index.canonical_identity_key(abroad, "s95") not in russian


def test_dashboard_tile_sums_unique_locations_once(
    client: TestClient, db_session: Session
) -> None:
    """Повторные поездки на одну площадку километров не добавляют."""
    auth_client = _authenticated_client(client)
    user = _current_user(auth_client, db_session)
    suffix = uuid4().hex[:8]

    home = _location(db_session, f"hd-home-{suffix}", "Домашний парк", MOSCOW)
    far = _location(db_session, f"hd-far-{suffix}", "Дальний парк", KHABAROVSK)
    near = _location(db_session, f"hd-near-{suffix}", "Соседний парк", NEARBY)

    _seed_runs(db_session, user, home, [date(2099, 1, 3), date(2099, 1, 10), date(2099, 1, 17)])
    # Дальняя площадка посещена дважды — в зачёт должна пойти один раз.
    _seed_runs(db_session, user, far, [date(2099, 2, 7), date(2099, 2, 14)])
    _seed_runs(db_session, user, near, [date(2099, 3, 7)])

    response = auth_client.get("/api/dashboard")
    assert response.status_code == 200, response.text
    payload = response.json()["stats"]["analytics"]["home_distance"]

    assert payload["home"]["name"] == "Домашний парк"
    assert payload["home"]["ambiguity"] is None
    expected = round_km(haversine_km(MOSCOW, KHABAROVSK)) + round_km(
        haversine_km(MOSCOW, NEARBY)
    )
    assert payload["total_distance_km"] == pytest.approx(expected, abs=1)
    assert payload["farthest"]["name"] == "Дальний парк"
    assert payload["visited_count"] == 3
    assert payload["unknown_count"] == 0


def test_dashboard_tile_flags_ambiguous_home(client: TestClient, db_session: Session) -> None:
    """Ничья по числу пробежек — повод показать красную подсказку на главной."""
    auth_client = _authenticated_client(client)
    user = _current_user(auth_client, db_session)
    suffix = uuid4().hex[:8]

    first = _location(db_session, f"hd-tie-a-{suffix}", "Аметистовый парк", MOSCOW)
    second = _location(db_session, f"hd-tie-b-{suffix}", "Бирюзовый парк", NEARBY)
    _seed_runs(db_session, user, first, [date(2099, 4, 4), date(2099, 4, 11)])
    _seed_runs(db_session, user, second, [date(2099, 5, 2), date(2099, 5, 9)])

    payload = auth_client.get("/api/dashboard").json()["stats"]["analytics"]["home_distance"]
    assert payload["home"]["ambiguity"] == "tie"
    assert payload["home"]["runner_up_name"] == "Бирюзовый парк"


def test_detail_lists_visited_and_excludes_them_from_unvisited(
    client: TestClient, db_session: Session
) -> None:
    auth_client = _authenticated_client(client)
    user = _current_user(auth_client, db_session)
    suffix = uuid4().hex[:8]

    home = _location(db_session, f"hd-d-home-{suffix}", "Стартовый парк", MOSCOW)
    far = _location(db_session, f"hd-d-far-{suffix}", "Восточный парк", KHABAROVSK)
    # Площадка каталога, где человек не был: должна попасть во вторую таблицу.
    unvisited = _location(db_session, f"hd-d-todo-{suffix}", "Непосещённый парк", NEARBY)

    _seed_runs(db_session, user, home, [date(2099, 6, 6), date(2099, 6, 13)])
    _seed_runs(db_session, user, far, [date(2099, 7, 4)])

    response = auth_client.get("/api/locations/visited/home-distance")
    assert response.status_code == 200, response.text
    payload = response.json()

    visited_names = [row["name"] for row in payload["visited"]]
    assert "Стартовый парк" in visited_names
    assert "Восточный парк" in visited_names
    # Посещённые отсортированы от дальней к ближней, дом с нулём — последним.
    assert payload["visited"][0]["name"] == "Восточный парк"
    home_row = next(row for row in payload["visited"] if row["is_home"])
    assert home_row["distance_km"] == 0

    unvisited_keys = {row["location_slug"] for row in payload["unvisited"]}
    assert unvisited.external_key in unvisited_keys
    assert home.external_key not in unvisited_keys
    assert far.external_key not in unvisited_keys
    todo_row = next(
        row for row in payload["unvisited"] if row["location_slug"] == unvisited.external_key
    )
    assert todo_row["distance_km"] == pytest.approx(round_km(haversine_km(MOSCOW, NEARBY)), abs=0.2)


def test_location_page_tile_marks_visited_and_unvisited(
    client: TestClient, db_session: Session
) -> None:
    auth_client = _authenticated_client(client)
    user = _current_user(auth_client, db_session)
    suffix = uuid4().hex[:8]

    home = _location(db_session, f"hd-p-home-{suffix}", "Родной парк", MOSCOW)
    far = _location(db_session, f"hd-p-far-{suffix}", "Тихоокеанский парк", KHABAROVSK)
    never = _location(db_session, f"hd-p-never-{suffix}", "Незнакомый парк", NEARBY)
    _seed_runs(db_session, user, home, [date(2099, 8, 1), date(2099, 8, 8)])
    _seed_runs(db_session, user, far, [date(2099, 9, 5)])

    visited = auth_client.get(f"/api/locations/page/{far.external_key}/me").json()
    assert visited["home_distance"]["visited"] is True
    assert visited["home_distance"]["is_home"] is False
    assert visited["home_distance"]["home_name"] == "Родной парк"
    assert visited["home_distance"]["distance_km"] == pytest.approx(
        round_km(haversine_km(MOSCOW, KHABAROVSK)), abs=1
    )

    untouched = auth_client.get(f"/api/locations/page/{never.external_key}/me").json()
    assert untouched["home_distance"]["visited"] is False
    assert untouched["home_distance"]["distance_km"] == pytest.approx(
        round_km(haversine_km(MOSCOW, NEARBY)), abs=0.2
    )

    own = auth_client.get(f"/api/locations/page/{home.external_key}/me").json()
    assert own["home_distance"]["is_home"] is True
    assert own["home_distance"]["distance_km"] == 0


def test_manual_home_location_moves_the_zero_point(
    client: TestClient, db_session: Session
) -> None:
    """Ручной выбор дома в настройках меняет и километры, и самый дальний старт."""
    auth_client = _authenticated_client(client)
    user = _current_user(auth_client, db_session)
    suffix = uuid4().hex[:8]

    frequent = _location(db_session, f"hd-m-freq-{suffix}", "Частый парк", MOSCOW)
    rare = _location(db_session, f"hd-m-rare-{suffix}", "Редкий парк", KHABAROVSK)
    _seed_runs(db_session, user, frequent, [date(2100, 1, 2), date(2100, 1, 9)])
    _seed_runs(db_session, user, rare, [date(2100, 2, 6)])

    auto = auth_client.get("/api/dashboard").json()["stats"]["analytics"]["home_distance"]
    assert auto["home"]["name"] == "Частый парк"

    candidates = auth_client.get("/api/settings/home-location/candidates").json()
    rare_key = next(item["catalog_identity_key"] for item in candidates if item["name"] == "Редкий парк")
    saved = auth_client.put("/api/settings/home-location", json={"catalog_identity_key": rare_key})
    assert saved.status_code == 200, saved.text

    manual = auth_client.get("/api/dashboard").json()["stats"]["analytics"]["home_distance"]
    assert manual["home"]["name"] == "Редкий парк"
    assert manual["home"]["is_auto"] is False
    assert manual["farthest"]["name"] == "Частый парк"
