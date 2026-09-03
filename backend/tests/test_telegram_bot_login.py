"""Вход подтверждением в боте: контекст «откуда вход», claim вкладкой, «Это не я»."""

from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.services.auth_identity_service import find_user_by_telegram_id

BOT_HEADERS = {"X-Bot-Secret": "bot-secret"}
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Mobile Safari/537.36"
)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        # Внешний сервис города в тестах не дёргаем.
        ip_geo_lookup_enabled=False,
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


def _start(client: TestClient, *, consent: bool = True) -> str:
    response = client.post(
        "/api/auth/login-request",
        params={"consent": "true"} if consent else {},
        headers={"User-Agent": CHROME_ANDROID},
    )
    assert response.status_code == 200
    return response.json()["request_token"]


def _confirm(client: TestClient, token: str, telegram_id: int) -> object:
    return client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": token,
            "telegram_id": telegram_id,
            "telegram_username": "runner",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers=BOT_HEADERS,
    )


def test_config_bot_login_follows_heartbeat(client: TestClient) -> None:
    before = client.get("/api/auth/telegram/config").json()
    assert before["bot_login"] is False

    heartbeat = client.post("/api/internal/bot/heartbeat", headers=BOT_HEADERS)
    assert heartbeat.status_code == 200

    after = client.get("/api/auth/telegram/config").json()
    assert after["bot_login"] is True
    assert after["bot_username"] == "TestBot"


def test_heartbeat_requires_bot_secret(client: TestClient) -> None:
    assert client.post("/api/internal/bot/heartbeat", headers={"X-Bot-Secret": "wrong"}).status_code == 403


def test_login_context_describes_request(client: TestClient) -> None:
    token = _start(client)
    response = client.post(
        "/api/auth/bot/login-context",
        json={"request_token": token, "telegram_id": 555000111},
        headers=BOT_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["browser"] == "Chrome"
    assert data["os"] == "Android"
    assert data["needs_consent"] is True
    assert data["consent_given"] is True
    assert data["link_mode"] is False
    assert data["requested_at_label"].startswith("сегодня в ")
    assert data["city"] == ""


def test_login_context_unknown_token_is_expired(client: TestClient) -> None:
    response = client.post(
        "/api/auth/bot/login-context",
        json={"request_token": "nope", "telegram_id": 1},
        headers=BOT_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "expired"


def test_confirm_then_tab_claims_session(client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    token = _start(client)
    pending = client.get(f"/api/auth/login-request/{token}/status").json()
    assert pending == {"status": "pending", "merge_token": None, "bot_alive": False}

    confirm = _confirm(client, token, 555000222)
    assert confirm.status_code == 200
    # Ссылка-страховка в боте осталась.
    assert "token=" in confirm.json()["magic_link"]

    status = client.get(f"/api/auth/login-request/{token}/status").json()
    assert status["status"] == "confirmed"

    claim = client.post(f"/api/auth/login-request/{token}/claim")
    assert claim.status_code == 200
    assert claim.json()["redirect"] == "welcome"
    assert "sr_session" in claim.cookies

    me = client.get("/api/auth/me", cookies={"sr_session": claim.cookies["sr_session"]})
    assert me.status_code == 200
    assert me.json()["telegram_id"] == 555000222

    assert client.get(f"/api/auth/login-request/{token}/status").json()["status"] == "claimed"
    # Второй раз забрать нельзя.
    assert client.post(f"/api/auth/login-request/{token}/claim").status_code == 409
    assert fake_redis.get(f"login_req_ctx:{token}") is None


def test_claim_before_confirmation_is_rejected(client: TestClient) -> None:
    token = _start(client)
    assert client.post(f"/api/auth/login-request/{token}/claim").status_code == 404
    assert client.post("/api/auth/login-request/unknown/claim").status_code == 404


def test_deny_in_bot_blocks_login(client: TestClient) -> None:
    token = _start(client)
    deny = client.post(
        "/api/auth/bot/deny",
        json={"request_token": token, "telegram_id": 555000333},
        headers=BOT_HEADERS,
    )
    assert deny.status_code == 200
    assert client.get(f"/api/auth/login-request/{token}/status").json()["status"] == "denied"

    # Подтвердить отклонённый запрос уже нельзя, забрать сессию — тем более.
    assert _confirm(client, token, 555000333).status_code in {404, 409, 410}
    assert client.post(f"/api/auth/login-request/{token}/claim").status_code == 404


def test_magic_link_still_works_after_claim(client: TestClient) -> None:
    """Страховка: ссылка в боте живёт своей жизнью, даже если вкладка вошла сама."""
    token = _start(client)
    magic_link = _confirm(client, token, 555000444).json()["magic_link"]
    assert client.post(f"/api/auth/login-request/{token}/claim").status_code == 200

    magic_token = magic_link.split("token=")[1]
    callback = client.get("/api/auth/callback", params={"token": magic_token}, follow_redirects=False)
    assert callback.status_code == 302


def test_consent_on_site_is_enough_for_the_bot(client: TestClient) -> None:
    """Галку поставили на сайте — бот подтверждает вход без второго вопроса.

    Бот показывает кнопку с текстом согласия только когда галки не было, а
    иначе шлёт consent_accepted=false. Раньше сервер смотрел лишь на этот флаг
    и отвечал «необходимо принять условия» человеку, который их только что
    принял (жалоба пользователя 03.09.2026).
    """
    token = _start(client, consent=True)
    response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": token,
            "telegram_id": 555000222,
            "telegram_username": "runner",
            "telegram_chat_id": 555000222,
            "consent_accepted": False,
        },
        headers=BOT_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["magic_link"]


def test_consent_recorded_when_it_came_from_the_site(
    client: TestClient, db_session: Session
) -> None:
    """Согласие с сайта попадает в профиль — иначе на следующем входе спросят снова."""
    token = _start(client, consent=True)
    client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": token,
            "telegram_id": 555000333,
            "telegram_username": "runner",
            "telegram_chat_id": 555000333,
            "consent_accepted": False,
        },
        headers=BOT_HEADERS,
    )
    user = find_user_by_telegram_id(db_session, 555000333)
    assert user is not None
    assert user.consent_accepted is True
    assert user.consent_ts is not None


def test_without_consent_anywhere_login_is_refused(client: TestClient) -> None:
    """Ни галки на сайте, ни кнопки в боте — вход по-прежнему не проходит."""
    token = _start(client, consent=False)
    response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": token,
            "telegram_id": 555000444,
            "telegram_username": "runner",
            "telegram_chat_id": 555000444,
            "consent_accepted": False,
        },
        headers=BOT_HEADERS,
    )
    assert response.status_code == 400
