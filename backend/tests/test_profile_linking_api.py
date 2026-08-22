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
