from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.core.abuse_store import remember_user_ip, set_ip_block


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
        mp.setattr("app.core.rate_limit.get_redis_client", lambda: fake_redis)
        mp.setattr("app.core.abuse_store.get_redis", lambda: fake_redis)
        mp.setattr("app.core.abuse_protection.get_redis", lambda: fake_redis)
        mp.setattr("app.services.auth_service.check_rate_limit", lambda *_args, **_kwargs: True)
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


def test_abuse_blocks_require_admin(client: TestClient) -> None:
    response = client.get("/api/admin/abuse/blocks")
    assert response.status_code == 401


def test_admin_list_and_create_abuse_ban(admin_client: TestClient) -> None:
    set_ip_block("198.51.100.10", 3600, source="auto", reason="test", created_by="system")
    remember_user_ip(424242, "203.0.113.5")

    list_response = admin_client.get("/api/admin/abuse/blocks")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert any(item["ip"] == "198.51.100.10" for item in payload["ip_blocks"])

    create_response = admin_client.post(
        "/api/admin/abuse/blocks",
        json={
            "target": "424242",
            "duration_seconds": 7200,
            "reason": "manual test",
            "ban_ip": True,
            "ban_account": True,
        },
    )
    assert create_response.status_code == 200

    list_after = admin_client.get("/api/admin/abuse/blocks").json()
    assert any(item["telegram_id"] == 424242 for item in list_after["telegram_bans"])
    assert any(item["ip"] == "203.0.113.5" for item in list_after["ip_blocks"])

    delete_ip = admin_client.delete("/api/admin/abuse/blocks/ip/203.0.113.5")
    assert delete_ip.status_code == 200

    delete_tg = admin_client.delete("/api/admin/abuse/blocks/telegram/424242")
    assert delete_tg.status_code == 200


def test_admin_ban_by_ip(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/admin/abuse/blocks",
        json={
            "target": "198.51.100.77",
            "duration_seconds": 3600,
            "reason": "spam",
            "ban_ip": True,
            "ban_account": False,
        },
    )
    assert response.status_code == 200

    listed = admin_client.get("/api/admin/abuse/blocks").json()
    assert any(item["ip"] == "198.51.100.77" for item in listed["ip_blocks"])

    clear_score = admin_client.post("/api/admin/abuse/blocks/ip/198.51.100.77/clear-score")
    assert clear_score.status_code == 200
