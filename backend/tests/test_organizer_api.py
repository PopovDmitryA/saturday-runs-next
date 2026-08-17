"""Кабинет организатора: доступ, «Долгая пауза», свод и экспорт."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, timedelta
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
    LocationOrganizerAccess,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.location_page_service import LOCATIONS_INDEX_CACHE_KEY
from app.services.organizer_access_service import (
    derive_organizer_identity_keys,
    invalidate_organizer_locations_cache,
)

ADMIN_TELEGRAM_ID = 9101


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


def _login(client: TestClient, telegram_id: int, username: str = "user") -> None:
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


def _platform(db_session: Session, code: str, name: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db_session.add(platform)
        db_session.flush()
    return platform


def _location(db_session: Session, platform: Platform, slug: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name=f"Локация {slug}",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()
    return location


def _event(
    db_session: Session,
    platform: Platform,
    location: Location,
    event_date: date,
    number: int,
    *,
    is_test: bool = False,
) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"org-{location.external_key}-{number}",
        event_date=event_date,
        event_number=number,
        title=f"Событие {number}",
        is_test_event=is_test,
    )
    db_session.add(event)
    db_session.flush()
    return event


def _participant(db_session: Session, platform: Platform, suffix: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"org-{suffix}",
        display_name=name,
        profile_url=f"https://example.test/{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def _link(db_session: Session, user: User, platform: Platform, participant: Participant) -> None:
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )
    db_session.flush()


def _run(
    db_session: Session,
    event: Event,
    participant: Participant,
    *,
    finish_time_sec: int | None = 1200,
    position: int | None = None,
) -> None:
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"run-{uuid4()}",
            position=position,
            finish_time_sec=finish_time_sec,
            finish_time_display="00:20:00" if finish_time_sec else None,
            status="finished",
        )
    )


def _volunteer(db_session: Session, event: Event, participant: Participant, role: str) -> None:
    db_session.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"vol-{uuid4()}",
            role=role,
        )
    )


def _current_user(db_session: Session, telegram_id: int) -> User:
    return db_session.query(User).filter(User.telegram_id == telegram_id).one()


# ===== Автодоступ по роли организатора =====


@pytest.mark.parametrize(
    "platform_code,platform_name,role",
    [
        ("five_verst", "5 вёрст", "Организатор"),
        ("runpark", "Runpark", "Руководитель"),
        # parkrun приклеивает к роли счётчик кредитов — сырое сравнение его не поймает.
        ("parkrun", "parkrun", "Run Director (12×)"),
        ("s95", "с95", "Директор 25"),
    ],
)
def test_organizer_role_aliases_grant_access(
    db_session: Session, platform_code: str, platform_name: str, role: str
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, platform_code, platform_name)
    location = _location(db_session, platform, f"org-loc-{suffix}")
    event = _event(db_session, platform, location, date(2026, 3, 7), 1)
    participant = _participant(db_session, platform, suffix, "Организатор Иванов")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"u{suffix}")
    db_session.add(user)
    db_session.flush()
    _link(db_session, user, platform, participant)
    _volunteer(db_session, event, participant, role)
    db_session.commit()

    keys = derive_organizer_identity_keys(db_session, user.id)
    assert len(keys) == 1


def test_non_organizer_volunteer_has_no_access(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    event = _event(db_session, platform, location, date(2026, 3, 7), 1)
    participant = _participant(db_session, platform, suffix, "Волонтёр Петров")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"u{suffix}")
    db_session.add(user)
    db_session.flush()
    _link(db_session, user, platform, participant)
    _volunteer(db_session, event, participant, "Секундомер")
    db_session.commit()

    assert derive_organizer_identity_keys(db_session, user.id) == set()


def test_test_events_do_not_grant_access(db_session: Session) -> None:
    """Организатор тестового события кабинет не открывает."""
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    event = _event(db_session, platform, location, date(2026, 3, 7), 1, is_test=True)
    participant = _participant(db_session, platform, suffix, "Организатор Теста")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"u{suffix}")
    db_session.add(user)
    db_session.flush()
    _link(db_session, user, platform, participant)
    _volunteer(db_session, event, participant, "Организатор")
    db_session.commit()

    assert derive_organizer_identity_keys(db_session, user.id) == set()


# ===== API: гейт доступа =====


def test_absence_forbidden_without_access(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    _event(db_session, platform, location, date(2026, 3, 7), 1)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)

    telegram_id = int(uuid4().int % 10_000_000_000)
    _login(client, telegram_id, "stranger")

    response = client.get(f"/api/organizer/{location.external_key}/absence")
    assert response.status_code == 403


def test_admin_passes_to_any_location(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    _event(db_session, platform, location, date(2026, 3, 7), 1)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)

    _login(client, ADMIN_TELEGRAM_ID, "admin_user")

    response = client.get(f"/api/organizer/{location.external_key}/absence")
    assert response.status_code == 200


def test_manual_grant_opens_access_and_menu_flag(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    _event(db_session, platform, location, date(2026, 3, 7), 1)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)

    telegram_id = int(uuid4().int % 10_000_000_000)
    _login(client, telegram_id, "granted")
    user = _current_user(db_session, telegram_id)

    assert client.get("/api/auth/me").json()["is_organizer"] is False
    assert client.get(f"/api/organizer/{location.external_key}/absence").status_code == 403

    from app.services.location_catalog_service import LocationCatalogIndex

    identity_key = LocationCatalogIndex(db_session).canonical_identity_key(location, "five_verst")
    db_session.add(LocationOrganizerAccess(user_id=user.id, location_key=identity_key))
    db_session.commit()
    invalidate_organizer_locations_cache(user.id)

    assert client.get(f"/api/organizer/{location.external_key}/absence").status_code == 200
    assert client.get("/api/auth/me").json()["is_organizer"] is True
    listing = client.get("/api/organizer/locations").json()
    assert listing["total"] == 1
    assert listing["items"][0]["access_source"] == "manual"


def test_admin_grant_crud_invalidates_cache(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    _event(db_session, platform, location, date(2026, 3, 7), 1)
    target = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"t{suffix}")
    db_session.add(target)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)

    _login(client, ADMIN_TELEGRAM_ID, "admin_user")

    from app.services.location_catalog_service import LocationCatalogIndex

    identity_key = LocationCatalogIndex(db_session).canonical_identity_key(location, "five_verst")

    created = client.post(
        f"/api/admin/users/{target.id}/organizer-access",
        json={"location_key": identity_key, "note": "оргкоманда"},
    )
    assert created.status_code == 201
    assert len(created.json()["manual"]) == 1

    grant_id = created.json()["manual"][0]["id"]
    deleted = client.delete(f"/api/admin/users/{target.id}/organizer-access/{grant_id}")
    assert deleted.status_code == 200
    assert deleted.json()["manual"] == []


def test_admin_grant_rejects_unknown_location_key(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    target = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        telegram_username=f"t{uuid4().int % 1_000_000}",
    )
    db_session.add(target)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)

    _login(client, ADMIN_TELEGRAM_ID, "admin_user")
    response = client.post(
        f"/api/admin/users/{target.id}/organizer-access",
        json={"location_key": "no-such-location-key"},
    )
    assert response.status_code == 404


# ===== «Долгая пауза» =====


def _absence_fixture(db_session: Session) -> tuple[Location, Platform, User]:
    """Локация с 6 событиями: «пропавший» бегал первые 3, активный — все."""
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    events = [
        _event(db_session, platform, location, date(2026, 1, 3) + timedelta(days=7 * i), i + 1)
        for i in range(6)
    ]
    lost = _participant(db_session, platform, f"{suffix}-lost", "Пропавший Иванов")
    active = _participant(db_session, platform, f"{suffix}-active", "Активный Петров")
    for event in events[:3]:
        _run(db_session, event, lost)
    for event in events:
        _run(db_session, event, active)

    organizer = _participant(db_session, platform, f"{suffix}-org", "Организатор Сидоров")
    _volunteer(db_session, events[0], organizer, "Организатор")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"u{suffix}")
    db_session.add(user)
    db_session.flush()
    _link(db_session, user, platform, organizer)
    db_session.commit()
    return location, platform, user


def test_absence_thresholds(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    location, _platform_obj, user = _absence_fixture(db_session)
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)
    _login(client, user.telegram_id or 0, "organizer")

    # Порог в 3 пробежки: «пропавший» (3 пробежки, 3 пропущенных события) виден.
    response = client.get(
        f"/api/organizer/{location.external_key}/absence",
        params={"min_runs": 3, "min_missed": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["items"]}
    assert "Пропавший Иванов" in names
    # Активный бегал на последнем событии — пропущенных нет, в выдачу не идёт.
    assert "Активный Петров" not in names
    lost_row = next(item for item in payload["items"] if item["name"] == "Пропавший Иванов")
    assert lost_row["runs_here"] == 3
    assert lost_row["missed_events"] == 3

    # Порог по пробежкам выше — не попадает никто.
    stricter = client.get(
        f"/api/organizer/{location.external_key}/absence",
        params={"min_runs": 5, "min_missed": 1},
    )
    assert stricter.json()["total"] == 0

    # Порог по пропускам выше числа пропущенных — тоже пусто.
    stricter_missed = client.get(
        f"/api/organizer/{location.external_key}/absence",
        params={"min_runs": 3, "min_missed": 4},
    )
    assert stricter_missed.json()["total"] == 0


# ===== Свод по пробежке =====


def _svod_fixture(db_session: Session) -> tuple[Location, Event, User]:
    suffix = str(uuid4().int % 1_000_000)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, platform, f"org-loc-{suffix}")
    past = _event(db_session, platform, location, date(2026, 2, 28), 1)
    today = _event(db_session, platform, location, date(2026, 3, 7), 2)

    newcomer = _participant(db_session, platform, f"{suffix}-new", "Новичок Иванов")
    _run(db_session, today, newcomer, finish_time_sec=1500, position=2)

    veteran = _participant(db_session, platform, f"{suffix}-vet", "Ветеран Петров")
    _run(db_session, past, veteran, finish_time_sec=1300)
    _run(db_session, today, veteran, finish_time_sec=1200, position=1)

    # Волонтёр с новой ролью: раньше был секундомером, сегодня — маршал.
    volunteer = _participant(db_session, platform, f"{suffix}-vol", "Волонтёр Сидоров")
    _volunteer(db_session, past, volunteer, "Секундомер")
    _volunteer(db_session, today, volunteer, "Маршал")

    organizer = _participant(db_session, platform, f"{suffix}-org", "Организатор Кузнецов")
    _volunteer(db_session, today, organizer, "Организатор")
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username=f"u{suffix}")
    db_session.add(user)
    db_session.flush()
    _link(db_session, user, platform, organizer)
    db_session.commit()
    return location, today, user


def test_svod_rows_and_flags(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    location, today, user = _svod_fixture(db_session)
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)
    _login(client, user.telegram_id or 0, "organizer")

    dates = client.get(f"/api/organizer/{location.external_key}/event-dates")
    assert dates.status_code == 200
    assert str(today.id) in {item["event_id"] for item in dates.json()["items"]}

    response = client.get(
        f"/api/organizer/{location.external_key}/event-report",
        params={"event_id": str(today.id)},
    )
    assert response.status_code == 200
    payload = response.json()

    runners = {row["name"]: row for row in payload["runners"]}
    assert runners["Новичок Иванов"]["first_in_system"] is True
    assert runners["Новичок Иванов"]["location_runs_count"] == 1
    # Ветеран улучшил время 1300 → 1200: личный рекорд и рекорд локации.
    assert runners["Ветеран Петров"]["is_pb"] is True
    assert runners["Ветеран Петров"]["is_location_pb"] is True
    assert runners["Ветеран Петров"]["location_runs_count"] == 2
    # Первым в протоколе идёт первое место.
    assert payload["runners"][0]["position"] == 1

    volunteers = {row["name"]: row for row in payload["volunteers"]}
    assert volunteers["Волонтёр Сидоров"]["new_roles"] == ["Маршал"]
    assert volunteers["Волонтёр Сидоров"]["first_volunteering"] is False
    assert volunteers["Организатор Кузнецов"]["first_volunteering"] is True


def test_svod_rejects_event_of_another_location(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    location, _today, user = _svod_fixture(db_session)
    platform = _platform(db_session, "five_verst", "5 вёрст")
    other_location = _location(db_session, platform, f"other-{uuid4().int % 1_000_000}")
    other_event = _event(db_session, platform, other_location, date(2026, 3, 7), 1)
    db_session.commit()
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)
    _login(client, user.telegram_id or 0, "organizer")

    response = client.get(
        f"/api/organizer/{location.external_key}/event-report",
        params={"event_id": str(other_event.id)},
    )
    assert response.status_code == 404


def test_svod_xlsx_export(
    client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    location, today, user = _svod_fixture(db_session)
    fake_redis.delete(LOCATIONS_INDEX_CACHE_KEY)
    _login(client, user.telegram_id or 0, "organizer")

    response = client.get(
        f"/api/organizer/{location.external_key}/event-report.xlsx",
        params={"event_id": str(today.id)},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment; filename=" in response.headers["content-disposition"]

    from io import BytesIO

    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames[:2] == ["Бегуны", "Волонтёры"]
    runners_sheet = workbook["Бегуны"]
    names = {row[1] for row in runners_sheet.iter_rows(min_row=2, values_only=True)}
    assert "Ветеран Петров" in names
