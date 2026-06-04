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
        with TestClient(app) as test_client:
            yield test_client

    app.dependency_overrides.clear()


def _authenticated_client(client: TestClient) -> TestClient:
    telegram_id = int(uuid4().int % 10_000_000_000)
    login_response = client.post("/api/auth/login-request")
    assert login_response.status_code == 200, login_response.text
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"notify_tester_{telegram_id}",
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


def test_notification_settings_default_disabled(client: TestClient) -> None:
    auth_client = _authenticated_client(client)
    response = auth_client.get("/api/settings/notifications")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert "Telegram" in data["description"]


def test_notification_settings_update(client: TestClient) -> None:
    auth_client = _authenticated_client(client)

    enable_response = auth_client.put("/api/settings/notifications", json={"enabled": True})
    assert enable_response.status_code == 200
    assert enable_response.json()["enabled"] is True

    get_response = auth_client.get("/api/settings/notifications")
    assert get_response.status_code == 200
    assert get_response.json()["enabled"] is True

    disable_response = auth_client.put("/api/settings/notifications", json={"enabled": False})
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled"] is False


def test_notification_settings_requires_auth(client: TestClient) -> None:
    assert client.get("/api/settings/notifications").status_code == 401
    assert client.put("/api/settings/notifications", json={"enabled": True}).status_code == 401
