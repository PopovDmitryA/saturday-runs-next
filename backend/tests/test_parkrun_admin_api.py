from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.parkrun.fetch.captcha_state import CAPTCHA_PENDING_KEY, set_captcha_pending


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def admin_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        admin_telegram_id=9001,
        parkrun_playwright_storage_state_path="/tmp/parkrun_test_state.json",
        parkrun_cdp_url="http://127.0.0.1:9222",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    admin_settings: Settings,
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: admin_settings

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.core.redis_client.get_redis_client", lambda: fake_redis)
        mp.setattr("app.core.rate_limit.get_redis_client", lambda: fake_redis)
        mp.setattr("app.parkrun.fetch.captcha_state.get_redis_client", lambda: fake_redis)
        mp.setattr("app.platform_fetch.cooldown.get_redis_client", lambda: fake_redis)
        mp.setattr("app.services.parkrun_admin_service.get_settings", lambda: admin_settings)
        mp.setattr("app.services.auth_service.check_rate_limit", lambda *_a, **_k: True)
        with TestClient(app) as test_client:
            yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(client: TestClient, admin_settings: Settings) -> TestClient:
    telegram_id = admin_settings.admin_telegram_id
    login_response = client.post("/api/auth/login-request")
    assert login_response.status_code == 200
    request_token = login_response.json()["request_token"]

    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": "admin_user",
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


def test_parkrun_session_requires_admin(client: TestClient) -> None:
    response = client.get("/api/admin/parkrun/session")
    assert response.status_code == 401


def test_parkrun_session_status(admin_client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    set_captcha_pending("preview: Human Verification")
    response = admin_client.get("/api/admin/parkrun/session")
    assert response.status_code == 200
    payload = response.json()
    assert payload["captcha_pending"] is True
    assert "Human Verification" in (payload["captcha_message"] or "")
    assert "9222" in payload["default_cdp_url"]
    assert fake_redis.get(CAPTCHA_PENDING_KEY) == "1"


def test_process_pending_blocked_while_captcha_pending(admin_client: TestClient) -> None:
    set_captcha_pending("blocked")
    response = admin_client.post("/api/admin/parkrun/process-pending", json={"limit": 5})
    assert response.status_code == 400
    assert "капч" in response.json()["detail"].lower()


def test_clear_captcha_wait(admin_client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    set_captcha_pending("test")
    response = admin_client.post("/api/admin/parkrun/clear-captcha-wait")
    assert response.status_code == 200
    assert fake_redis.get(CAPTCHA_PENDING_KEY) is None
