from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import Platform
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

    with patch("app.core.redis_client.get_redis_client", return_value=fake_redis):
        with patch("app.services.auth_service.check_rate_limit", return_value=True):
            with patch("app.services.sync_trigger_service.enqueue_user_sync"):
                with TestClient(app) as test_client:
                    yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(client: TestClient) -> TestClient:
    telegram_id = int(uuid4().int % 10_000_000_000)
    login_response = client.post("/api/auth/login-request")
    assert login_response.status_code == 200, login_response.text
    request_token = login_response.json()["request_token"]

    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"s95_link_tester_{telegram_id}",
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


def test_s95_preview_fetches_parkrun_match_for_short_barcode(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    profile_url = "https://s95.ru/athletes/5207/"
    s95_preview = ProfilePreview(
        external_user_id="5207",
        display_name="Test S95",
        profile_url=profile_url,
        barcode_id="A7035519",
        parkrun_eligible=True,
        platform_code="s95",
    )
    parkrun_preview = ProfilePreview(
        external_user_id="7035519",
        display_name="Test Parkrun",
        profile_url="https://www.parkrun.org.uk/parkrunner/7035519/",
        total_runs=10,
        platform_code="parkrun",
    )

    with patch(
        "app.api.routes.profiles.preview_s95_profile_link",
        return_value=(s95_preview, parkrun_preview),
    ) as preview_mock:
        response = authenticated_client.post(
            "/api/profiles/s95/preview",
            json={"profile_url": profile_url},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Test S95"
    assert data["parkrun_match"]["display_name"] == "Test Parkrun"
    assert preview_mock.call_count == 1


def test_s95_preview_without_parkrun_match_when_barcode_long(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    profile_url = "https://s95.ru/athletes/5207/"
    s95_preview = ProfilePreview(
        external_user_id="5207",
        display_name="Test S95",
        profile_url=profile_url,
        barcode_id="A770005892",
        parkrun_eligible=False,
        platform_code="s95",
    )

    with patch(
        "app.api.routes.profiles.preview_s95_profile_link",
        return_value=(s95_preview, None),
    ) as preview_mock:
        response = authenticated_client.post(
            "/api/profiles/s95/preview",
            json={"profile_url": profile_url},
        )

    assert response.status_code == 200
    assert response.json()["parkrun_match"] is None
    assert preview_mock.call_count == 1


def test_s95_preview_returns_s95_when_parkrun_playwright_fails(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    profile_url = "https://s95.ru/athletes/5207/"
    s95_preview = ProfilePreview(
        external_user_id="5207",
        display_name="Test S95",
        profile_url=profile_url,
        barcode_id="A7035519",
        parkrun_eligible=True,
        platform_code="s95",
    )

    with patch(
        "app.api.routes.profiles.preview_s95_profile_link",
        return_value=(s95_preview, None),
    ):
        response = authenticated_client.post(
            "/api/profiles/s95/preview",
            json={"profile_url": profile_url},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Test S95"
    assert data["parkrun_match"] is None


def test_preview_s95_profile_link_skips_parkrun_when_already_linked(
    db_session: Session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    profile_url = "https://s95.ru/athletes/5207/"
    s95_preview = ProfilePreview(
        external_user_id="5207",
        display_name="Test S95",
        profile_url=profile_url,
        barcode_id="A7035519",
        parkrun_eligible=True,
        platform_code="s95",
    )
    user = type("User", (), {"id": uuid4()})()

    with patch(
        "app.services.profile_linking_service.preview_profile_link",
        return_value=s95_preview,
    ) as preview_mock:
        with patch(
            "app.services.profile_linking_service._user_has_platform_link",
            return_value=True,
        ):
            from app.services.profile_linking_service import preview_s95_profile_link

            s95, parkrun = preview_s95_profile_link(db_session, profile_url, user=user)

    assert s95.display_name == "Test S95"
    assert parkrun is None
    preview_mock.assert_called_once()


def test_preview_s95_profile_link_swallows_parkrun_errors(
    db_session: Session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    profile_url = "https://s95.ru/athletes/5207/"
    s95_preview = ProfilePreview(
        external_user_id="5207",
        display_name="Test S95",
        profile_url=profile_url,
        barcode_id="A7035519",
        parkrun_eligible=True,
        platform_code="s95",
    )
    user = type("User", (), {"id": uuid4()})()

    def preview_side_effect(_db, platform_code, _url, *, user=None):
        if platform_code == "s95":
            return s95_preview
        raise ProfileLinkingError("not found", 404)

    from app.services.profile_linking_service import ProfileLinkingError, preview_s95_profile_link

    with patch(
        "app.services.profile_linking_service.preview_profile_link",
        side_effect=preview_side_effect,
    ):
        with patch("app.s95.fetch.browser.shutdown_browser"):
            s95, parkrun = preview_s95_profile_link(db_session, profile_url, user=user)

    assert s95.display_name == "Test S95"
    assert parkrun is None


def test_s95_confirm_can_link_both_profiles(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    s95_platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    parkrun_platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if s95_platform is None or parkrun_platform is None:
        pytest.skip("platforms not seeded")

    profile_url = "https://s95.ru/athletes/5207/"
    s95_external_id = str(uuid4().int % 1_000_000)
    parkrun_external_id = "7035519"

    s95_preview = ProfilePreview(
        external_user_id=s95_external_id,
        display_name="Test S95",
        profile_url=profile_url,
        barcode_id="A7035519",
        parkrun_eligible=True,
        platform_code="s95",
    )
    parkrun_preview = ProfilePreview(
        external_user_id=parkrun_external_id,
        display_name="Test Parkrun",
        profile_url=f"https://www.parkrun.org.uk/parkrunner/{parkrun_external_id}/",
        platform_code="parkrun",
    )

    with patch(
        "app.services.profile_linking_service.get_cached_profile_preview",
        side_effect=lambda _uid, platform_code, _url: {
            "s95": s95_preview,
            "parkrun": parkrun_preview,
        }[platform_code],
    ):
        with patch("app.services.profile_linking_service.confirm_profile_link") as confirm_mock:
            s95_link = type("Link", (), {"id": uuid4()})()
            parkrun_link = type("Link", (), {"id": uuid4()})()
            confirm_mock.side_effect = [s95_link, parkrun_link]

            with patch(
                "app.api.routes.profiles.list_user_profile_links",
                return_value=[
                    {
                        "id": s95_link.id,
                        "platform_code": "s95",
                        "platform_name": "S95",
                        "external_user_id": s95_external_id,
                        "external_url": profile_url,
                        "display_name": "Test S95",
                        "sync_status": "syncing",
                        "last_user_sync_at": None,
                    },
                    {
                        "id": parkrun_link.id,
                        "platform_code": "parkrun",
                        "platform_name": "parkrun",
                        "external_user_id": parkrun_external_id,
                        "external_url": parkrun_preview.profile_url,
                        "display_name": "Test Parkrun",
                        "sync_status": "syncing",
                        "last_user_sync_at": None,
                    },
                ],
            ):
                response = authenticated_client.post(
                    "/api/profiles/s95/confirm",
                    json={"profile_url": profile_url, "link_parkrun": True},
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["link"]["platform_code"] == "s95"
    assert payload["parkrun_link"]["platform_code"] == "parkrun"
    assert confirm_mock.call_count == 2
