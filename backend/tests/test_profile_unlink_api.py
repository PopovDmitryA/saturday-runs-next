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
from app.models import Platform, PlatformLink
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
def client(
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    auth_settings: Settings,
) -> Generator[TestClient, None, None]:
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
            "telegram_username": f"unlink_tester_{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200, confirm_response.text
    magic_link = confirm_response.json()["magic_link"]
    callback = client.get(magic_link, follow_redirects=False)
    assert callback.status_code in (302, 307), callback.text
    return client


def test_unlink_requires_auth(client: TestClient) -> None:
    response = client.delete("/api/profiles/five_verst")
    assert response.status_code == 401


def test_unlink_unknown_platform(authenticated_client: TestClient) -> None:
    response = authenticated_client.delete("/api/profiles/unknown_platform")
    assert response.status_code == 404


def test_unlink_not_linked(authenticated_client: TestClient) -> None:
    response = authenticated_client.delete("/api/profiles/five_verst")
    assert response.status_code == 404


def test_unlink_removes_platform_link(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    preview = ProfilePreview(
        platform_code="five_verst",
        external_user_id=f"unlink_{uuid4().hex[:8]}",
        display_name="Unlink Test",
        profile_url="https://5verst.ru/userstats/9990001/",
        total_runs=1,
        total_volunteering=0,
        club_name=None,
        barcode_id=None,
        planning_location=None,
        age_category=None,
        parkrun_eligible=False,
    )

    with patch(
        "app.platform_adapters.five_verst.adapter.FiveVerstAdapter.fetch_profile_preview",
        return_value=preview,
    ):
        confirm_response = authenticated_client.post(
            "/api/profiles/five-verst/confirm",
            json={"profile_url": preview.profile_url},
        )
    assert confirm_response.status_code == 200, confirm_response.text

    unlink_response = authenticated_client.delete("/api/profiles/five_verst")
    assert unlink_response.status_code == 200, unlink_response.text
    assert unlink_response.json()["message"] == "unlinked"

    db_session.expire_all()
    assert (
        db_session.query(PlatformLink)
        .filter(
            PlatformLink.external_user_id == preview.external_user_id,
            PlatformLink.platform_id == platform.id,
        )
        .count()
        == 0
    )

    list_response = authenticated_client.get("/api/profiles")
    assert list_response.status_code == 200
    codes = [item["platform_code"] for item in list_response.json()]
    assert "five_verst" not in codes
