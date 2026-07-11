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
from app.models import BlockedProfileSlug


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
        admin_telegram_id=9001,
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


def _login(client: TestClient, *, telegram_id: int, username: str) -> None:
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
    token = confirm_response.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)


def _login_regular(client: TestClient) -> None:
    telegram_id = int(uuid4().int % 10_000_000_000)
    _login(client, telegram_id=telegram_id, username=f"user_{telegram_id}")


def _login_admin(client: TestClient) -> None:
    _login(client, telegram_id=9001, username="admin_user")


def test_migration_seeds_reserved_handles(client: TestClient) -> None:
    """Три адреса из миграции 041 должны быть недоступны конечному пользователю."""
    _login_regular(client)
    for handle in ("engirunners", "dmitripetrov", "engirunner"):
        response = client.get("/api/settings/profile-slug/check", params={"slug": handle})
        assert response.status_code == 200
        assert response.json()["available"] is False, handle


def test_blocked_slug_check_reports_unavailable(client: TestClient, db_session: Session) -> None:
    db_session.add(BlockedProfileSlug(slug="reservedhandle", comment="reserved"))
    db_session.commit()

    _login_regular(client)
    # Регистронезависимо: вводим в верхнем регистре — всё равно занято.
    response = client.get("/api/settings/profile-slug/check", params={"slug": "ReservedHandle"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["normalized"] == "reservedhandle"
    assert payload["available"] is False
    assert payload["reason"]


def test_blocked_slug_cannot_be_claimed(client: TestClient, db_session: Session) -> None:
    db_session.add(BlockedProfileSlug(slug="blockedhandle", comment="reserved"))
    db_session.commit()

    _login_regular(client)
    response = client.put("/api/settings/profile-slug", json={"slug": "blockedhandle"})
    assert response.status_code == 400


def test_admin_can_reserve_system_reserved_word(client: TestClient) -> None:
    """В ручной блок-лист можно занести системное слово (admin), хотя обычному
    пользователю оно как public_slug запрещено."""
    _login_admin(client)
    create = client.post("/api/admin/profile-slugs/blocked", json={"slug": "admin"})
    assert create.status_code == 201, create.text
    assert create.json()["slug"] == "admin"


def test_reserved_word_still_forbidden_for_user_slug(client: TestClient) -> None:
    _login_regular(client)
    response = client.put("/api/settings/profile-slug", json={"slug": "admin"})
    assert response.status_code == 400


def test_admin_can_manage_blocklist(client: TestClient) -> None:
    _login_admin(client)

    create = client.post(
        "/api/admin/profile-slugs/blocked",
        json={"slug": "CustomReserved", "comment": "Дима Петров попросил"},
    )
    assert create.status_code == 201, create.text
    created = create.json()
    assert created["slug"] == "customreserved"  # нормализовано в нижний регистр
    assert created["comment"] == "Дима Петров попросил"

    # Повторная попытка — конфликт.
    duplicate = client.post("/api/admin/profile-slugs/blocked", json={"slug": "customreserved"})
    assert duplicate.status_code == 409

    listing = client.get("/api/admin/profile-slugs/blocked")
    assert listing.status_code == 200
    assert any(item["slug"] == "customreserved" for item in listing.json()["items"])

    delete = client.delete(f"/api/admin/profile-slugs/blocked/{created['id']}")
    assert delete.status_code == 200

    after = client.get("/api/admin/profile-slugs/blocked")
    assert all(item["slug"] != "customreserved" for item in after.json()["items"])


def test_blocklist_requires_admin(client: TestClient) -> None:
    _login_regular(client)
    assert client.get("/api/admin/profile-slugs/blocked").status_code == 403
    assert client.post("/api/admin/profile-slugs/blocked", json={"slug": "whatever"}).status_code == 403


def test_admin_user_list_exposes_public_slug(client: TestClient) -> None:
    telegram_id = int(uuid4().int % 10_000_000_000)
    username = f"slugowner_{telegram_id}"
    _login(client, telegram_id=telegram_id, username=username)
    set_slug = client.put("/api/settings/profile-slug", json={"slug": "runner-vasya"})
    assert set_slug.status_code == 200, set_slug.text
    assert set_slug.json()["slug"] == "runner-vasya"

    # Тот же клиент логинится админом (перетирает cookie сессии).
    _login_admin(client)
    listing = client.get("/api/admin/users", params={"q": username})
    assert listing.status_code == 200
    item = next(row for row in listing.json()["items"] if row["telegram_username"] == username)
    assert item["public_slug"] == "runner-vasya"
