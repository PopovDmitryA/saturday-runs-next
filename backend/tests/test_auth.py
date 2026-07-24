from __future__ import annotations

from collections.abc import Generator

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

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_login_request_creates_pending_status(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    response = client.post("/api/auth/login-request")
    assert response.status_code == 200
    data = response.json()
    assert "request_token" in data
    assert data["bot_url"].startswith("https://t.me/TestBot?start=login_")
    assert fake_redis.get(f"login_req:{data['request_token']}") == "pending"


def test_bot_confirm_and_callback_flow(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]

    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": 123456789,
            "telegram_username": "runner",
            "telegram_chat_id": 123456789,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200
    magic_link = confirm_response.json()["magic_link"]
    assert "token=" in magic_link

    token = magic_link.split("token=")[1]
    callback_response = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    assert callback_response.status_code == 302
    assert callback_response.headers["location"] == "http://testserver/dashboard"
    assert "sr_session" in callback_response.cookies

    session_cookie = callback_response.cookies["sr_session"]
    me_response = client.get("/api/auth/me", cookies={"sr_session": session_cookie})
    assert me_response.status_code == 200
    assert me_response.json()["telegram_id"] == 123456789
    assert me_response.json()["consent_accepted"] is True


def test_bot_confirm_requires_consent_for_new_user(client: TestClient) -> None:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]

    response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": 424242424,
            "telegram_chat_id": 424242424,
            "consent_accepted": False,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert response.status_code == 400


def test_bot_login_status_for_new_user(client: TestClient) -> None:
    response = client.post(
        "/api/auth/bot/login-status",
        json={"telegram_id": 999888777},
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert response.status_code == 200
    assert response.json()["needs_consent"] is True


def test_bot_confirm_rejects_invalid_secret(client: TestClient) -> None:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]

    response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": 1,
            "telegram_chat_id": 1,
        },
        headers={"X-Bot-Secret": "wrong"},
    )
    assert response.status_code == 403


def test_magic_link_single_use(client: TestClient) -> None:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]

    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": 987654321,
            "telegram_chat_id": 987654321,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    magic_link = confirm_response.json()["magic_link"]
    token = magic_link.split("token=")[1]

    first = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    assert first.status_code == 302

    second = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    assert second.status_code == 409


def test_logout_clears_session(client: TestClient) -> None:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": 555,
            "telegram_chat_id": 555,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm_response.json()["magic_link"].split("token=")[1]
    callback_response = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    session_cookie = callback_response.cookies["sr_session"]

    logout_response = client.post("/api/auth/logout", cookies={"sr_session": session_cookie})
    assert logout_response.status_code == 200

    me_response = client.get("/api/auth/me", cookies={"sr_session": session_cookie})
    assert me_response.status_code == 401


def _login_session(client: TestClient, telegram_id: int, *, username: str | None = None) -> str:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
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
    assert confirm_response.status_code == 200
    token = confirm_response.json()["magic_link"].split("token=")[1]
    callback_response = client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    assert callback_response.status_code == 302
    return callback_response.cookies["sr_session"]


def test_session_slides_on_me(
    client: TestClient, fake_redis: fakeredis.FakeRedis, auth_settings: Settings
) -> None:
    session_cookie = _login_session(client, 777000111)

    session_key = next(k for k in fake_redis.keys("session:*"))
    fake_redis.expire(session_key, 60)
    assert fake_redis.ttl(session_key) <= 60

    me_response = client.get("/api/auth/me", cookies={"sr_session": session_cookie})
    assert me_response.status_code == 200

    assert fake_redis.ttl(session_key) > auth_settings.session_ttl_seconds - 60
    set_cookie = me_response.headers.get("set-cookie", "")
    assert "sr_session=" in set_cookie
    assert f"Max-Age={auth_settings.session_ttl_seconds}" in set_cookie


def test_display_name_can_be_customized_and_reset(client: TestClient) -> None:
    session_cookie = _login_session(client, 424242424, username="runner_login")

    customize_response = client.patch(
        "/api/auth/me",
        json={"display_name": "Мой кастомный ник"},
        cookies={"sr_session": session_cookie},
    )
    assert customize_response.status_code == 200
    customized = customize_response.json()
    assert customized["display_name"] == "Мой кастомный ник"
    assert customized["display_name_customized"] is True

    reset_response = client.patch(
        "/api/auth/me",
        json={"display_name": ""},
        cookies={"sr_session": session_cookie},
    )
    assert reset_response.status_code == 200
    reset = reset_response.json()
    assert reset["display_name_customized"] is False
    assert reset["display_name"] == "runner_login"
    assert reset["telegram_username"] == "runner_login"
