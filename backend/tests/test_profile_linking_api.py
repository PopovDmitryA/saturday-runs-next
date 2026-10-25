from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult
from app.platform_adapters.canonical import ProfilePreview


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

    with patch("app.services.auth_service.check_rate_limit", return_value=True):
        with patch("app.services.sync_trigger_service.enqueue_user_sync"):
            with TestClient(app) as test_client:
                yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(client: TestClient, fake_redis: fakeredis.FakeRedis) -> TestClient:
    telegram_id = int(uuid4().int % 10_000_000_000)
    login_response = client.post("/api/auth/login-request")
    assert login_response.status_code == 200, login_response.text
    request_token = login_response.json()["request_token"]

    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"link_tester_{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200
    magic_link = confirm_response.json()["magic_link"]
    token = magic_link.split("token=")[-1]
    callback_response = client.get(f"/api/auth/callback?token={token}", follow_redirects=False)
    assert callback_response.status_code == 302
    return client


def test_profile_preview_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/api/profiles/five-verst/preview",
        json={"profile_url": "https://5verst.ru/userstats/12345/"},
    )
    assert response.status_code == 401


def test_profile_preview_and_confirm_flow(authenticated_client: TestClient, db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    external_user_id = str(uuid4().int % 1_000_000_000)
    profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Дмитрий ПОПОВ",
        profile_url=profile_url,
        total_runs=148,
        total_volunteering=46,
        platform_code="five_verst",
    )

    with patch(
        "app.services.profile_linking_service.persist_live_profile_preview",
        return_value=preview,
    ):
        preview_response = authenticated_client.post(
            "/api/profiles/five-verst/preview",
            json={"profile_url": profile_url},
        )
        assert preview_response.status_code == 200
        data = preview_response.json()
        assert data["display_name"] == "Дмитрий ПОПОВ"
        assert data["total_runs"] == 148

        confirm_response = authenticated_client.post(
            "/api/profiles/five-verst/confirm",
            json={"profile_url": profile_url},
        )
        assert confirm_response.status_code == 200
        link = confirm_response.json()["link"]
        assert link["platform_code"] == "five_verst"
        assert link["external_user_id"] == external_user_id

        list_response = authenticated_client.get("/api/profiles")
        assert list_response.status_code == 200
        links = list_response.json()
        assert any(item["external_user_id"] == external_user_id for item in links)


def test_confirm_from_registration_form_marks_signup(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """Номер из формы «введите полученный номер» на /registration привязывается
    со способом signup — по нему считаются регистрации через сайт. Без флага
    способ прежний: ссылка."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    external_user_id = str(uuid4().int % 1_000_000_000)
    profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Свежий УЧАСТНИК",
        profile_url=profile_url,
        total_runs=0,
        total_volunteering=0,
        platform_code="five_verst",
    )

    with patch(
        "app.services.profile_linking_service.persist_live_profile_preview",
        return_value=preview,
    ):
        assert (
            authenticated_client.post(
                "/api/profiles/five-verst/preview", json={"profile_url": profile_url}
            ).status_code
            == 200
        )
        confirm = authenticated_client.post(
            "/api/profiles/five-verst/confirm",
            json={"profile_url": profile_url, "signup": True},
        )
        assert confirm.status_code == 200

    link = (
        db_session.query(PlatformLink)
        .filter(PlatformLink.platform_id == platform.id, PlatformLink.external_user_id == external_user_id)
        .one()
    )
    assert link.link_method == "signup"


def _signup_confirm(
    client: TestClient, external_user_id: str, preview: ProfilePreview, started_at: datetime
) -> None:
    profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    with patch(
        "app.services.profile_linking_service.persist_live_profile_preview",
        return_value=preview,
    ):
        assert client.post("/api/profiles/five-verst/preview", json={"profile_url": profile_url}).status_code == 200
        confirm = client.post(
            "/api/profiles/five-verst/confirm",
            json={"profile_url": profile_url, "signup": True, "signup_started_at": started_at.isoformat()},
        )
        assert confirm.status_code == 200


def test_signup_with_activity_before_click_is_plain_link(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """Нажал «Зарегистрироваться», а привязал профиль, который уже бегал, — это
    старый аккаунт, а не регистрация через сайт (решение Дмитрия 25.10.2026).
    Протоколов в нашей базе может не быть — хватает счётчика профиля."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    external_user_id = str(uuid4().int % 1_000_000_000)
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Старый УЧАСТНИК",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
        total_runs=12,
        total_volunteering=3,
        platform_code="five_verst",
    )

    _signup_confirm(authenticated_client, external_user_id, preview, datetime.now(timezone.utc))

    link = (
        db_session.query(PlatformLink)
        .filter(PlatformLink.platform_id == platform.id, PlatformLink.external_user_id == external_user_id)
        .one()
    )
    assert link.link_method == "url"


def test_signup_run_on_click_day_still_counts(authenticated_client: TestClient, db_session: Session) -> None:
    """Зарегистрировался утром и в тот же день пробежал: старт в день клика
    регистрацию через сайт не отменяет — он уже после нажатия."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    external_user_id = str(uuid4().int % 1_000_000_000)
    started_at = datetime.now(timezone.utc)
    click_day = started_at.astimezone(ZoneInfo("Europe/Moscow")).date()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Свежий БЕГУН",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
    )
    location = Location(
        platform_id=platform.id,
        external_key=f"signup-{uuid4().hex[:10]}",
        name="Signup Park",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([participant, location])
    db_session.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"signup:{location.external_key}:1",
        event_date=click_day,
        event_number=1,
        title="Signup #1",
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{external_user_id}",
            position=1,
            finish_time_sec=25 * 60,
            finish_time_display="00:25:00",
        )
    )
    db_session.commit()

    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Свежий БЕГУН",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
        total_runs=1,
        total_volunteering=0,
        platform_code="five_verst",
    )
    _signup_confirm(authenticated_client, external_user_id, preview, started_at)

    link = (
        db_session.query(PlatformLink)
        .filter(PlatformLink.platform_id == platform.id, PlatformLink.external_user_id == external_user_id)
        .one()
    )
    assert link.link_method == "signup"


def _activity_for(
    db_session: Session,
    platform: Platform,
    external_user_id: str,
    event_date,
    *,
    is_test_event: bool = False,
) -> None:
    """Пробежка участника в нашей базе — до привязки, как протокол из синка."""
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Участник С ИСТОРИЕЙ",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
    )
    location = Location(
        platform_id=platform.id,
        external_key=f"signup-{uuid4().hex[:10]}",
        name="Signup History Park",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([participant, location])
    db_session.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"signup:{location.external_key}:1",
        event_date=event_date,
        event_number=1,
        title="Signup history #1",
        is_test_event=is_test_event,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:{external_user_id}",
            position=1,
            finish_time_sec=24 * 60,
            finish_time_display="00:24:00",
        )
    )
    db_session.commit()


def _five_verst_link_method(db_session: Session, platform: Platform, external_user_id: str) -> str:
    return (
        db_session.query(PlatformLink)
        .filter(PlatformLink.platform_id == platform.id, PlatformLink.external_user_id == external_user_id)
        .one()
        .link_method
    )


def test_signup_with_protocol_before_click_is_plain_link(
    authenticated_client: TestClient, db_session: Session
) -> None:
    """Счётчик профиля пустой, но в нашей базе есть его пробежка раньше дня
    клика — значит, аккаунт старый."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    external_user_id = str(uuid4().int % 1_000_000_000)
    started_at = datetime.now(timezone.utc)
    click_day = started_at.astimezone(ZoneInfo("Europe/Moscow")).date()
    _activity_for(db_session, platform, external_user_id, click_day - timedelta(days=7))
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Участник С ИСТОРИЕЙ",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
        total_runs=0,
        total_volunteering=0,
        platform_code="five_verst",
    )

    _signup_confirm(authenticated_client, external_user_id, preview, started_at)

    assert _five_verst_link_method(db_session, platform, external_user_id) == "url"


def test_signup_ignores_test_events_before_click(authenticated_client: TestClient, db_session: Session) -> None:
    """Тестовый старт (забег №0) активностью не считается — как и везде на сайте."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    external_user_id = str(uuid4().int % 1_000_000_000)
    started_at = datetime.now(timezone.utc)
    click_day = started_at.astimezone(ZoneInfo("Europe/Moscow")).date()
    _activity_for(db_session, platform, external_user_id, click_day - timedelta(days=7), is_test_event=True)
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Участник С ИСТОРИЕЙ",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
        total_runs=0,
        total_volunteering=0,
        platform_code="five_verst",
    )

    _signup_confirm(authenticated_client, external_user_id, preview, started_at)

    assert _five_verst_link_method(db_session, platform, external_user_id) == "signup"


def test_signup_old_click_time_is_not_trusted(authenticated_client: TestClient, db_session: Session) -> None:
    """Время клика старше жизни намерения — не доверяем ему: иначе старой
    датой можно было бы обойти правило «активность до клика»."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")
    external_user_id = str(uuid4().int % 1_000_000_000)
    now = datetime.now(timezone.utc)
    _activity_for(
        db_session, platform, external_user_id, now.astimezone(ZoneInfo("Europe/Moscow")).date() - timedelta(days=7)
    )
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Участник С ИСТОРИЕЙ",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
        total_runs=0,
        total_volunteering=0,
        platform_code="five_verst",
    )

    _signup_confirm(authenticated_client, external_user_id, preview, now - timedelta(days=365))

    assert _five_verst_link_method(db_session, platform, external_user_id) == "url"


def test_s95_confirm_from_registration_form_marks_signup(
    authenticated_client: TestClient, db_session: Session
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")
    athlete_id = str(800_000 + uuid4().int % 100_000)
    profile_url = f"https://s95.ru/athletes/{athlete_id}"
    preview = ProfilePreview(
        external_user_id=athlete_id,
        display_name="Новый УЧАСТНИК",
        profile_url=profile_url,
        total_runs=None,
        total_volunteering=None,
        platform_code="s95",
    )
    with patch(
        "app.services.profile_linking_service.persist_live_profile_preview",
        return_value=preview,
    ):
        assert authenticated_client.post("/api/profiles/s95/preview", json={"profile_url": profile_url}).status_code == 200
        confirm = authenticated_client.post(
            "/api/profiles/s95/confirm",
            json={
                "profile_url": profile_url,
                "signup": True,
                "signup_started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert confirm.status_code == 200

    link = (
        db_session.query(PlatformLink)
        .filter(PlatformLink.platform_id == platform.id, PlatformLink.external_user_id == athlete_id)
        .one()
    )
    assert link.link_method == "signup"


def test_profile_confirm_rejects_duplicate_link(authenticated_client: TestClient, db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    external_user_id = str(uuid4().int % 1_000_000_000)
    profile_url = f"https://5verst.ru/userstats/{external_user_id}/"
    preview = ProfilePreview(
        external_user_id=external_user_id,
        display_name="Test Runner",
        profile_url=profile_url,
        platform_code="five_verst",
    )

    with patch(
        "app.services.profile_linking_service.persist_live_profile_preview",
        return_value=preview,
    ):
        preview_response = authenticated_client.post(
            "/api/profiles/five-verst/preview",
            json={"profile_url": profile_url},
        )
        assert preview_response.status_code == 200

        first = authenticated_client.post(
            "/api/profiles/five-verst/confirm",
            json={"profile_url": profile_url},
        )
        assert first.status_code == 200

        second = authenticated_client.post(
            "/api/profiles/five-verst/confirm",
            json={"profile_url": profile_url},
        )
        assert second.status_code == 409
