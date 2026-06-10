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
from app.models import AuthIdentity, AuthProvider, Participant, Platform, PlatformLink, User


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
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


def _login_admin(client: TestClient) -> None:
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": 9001,
            "telegram_username": "admin_user",
            "telegram_chat_id": 9001,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm_response.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)


def test_admin_users_requires_admin(client: TestClient, db_session: Session) -> None:
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), telegram_username="regular")
    db_session.add(user)
    db_session.flush()
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": user.telegram_id,
            "telegram_chat_id": user.telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm_response.json()["magic_link"].split("token=")[1]
    client.get("/api/auth/callback", params={"token": token}, follow_redirects=False)
    response = client.get("/api/admin/users")
    assert response.status_code == 403


def test_admin_users_search_and_preview(client: TestClient, db_session: Session) -> None:
    target = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        telegram_username="runner_target",
        telegram_chat_id=123,
        display_name="Target Runner",
        news_subscribed=True,
        consent_accepted=True,
    )
    db_session.add(target)
    db_session.flush()

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    external_id = str(uuid4().int % 1_000_000_000)
    db_session.add(
        PlatformLink(
            user_id=target.id,
            platform_id=platform.id,
            external_user_id=external_id,
            external_url=f"https://5verst.ru/userstats/{external_id}/",
        )
    )
    db_session.commit()

    _login_admin(client)

    list_response = client.get("/api/admin/users", params={"q": "runner_target"})
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] >= 1
    item = next(row for row in payload["items"] if row["telegram_username"] == "runner_target")
    assert item["news_subscribed"] is True
    assert len(item["platform_links"]) == 1
    assert item["platform_links"][0]["platform_code"] == "five_verst"
    assert item["platform_links"][0]["external_user_id"] == external_id

    preview = client.get(f"/api/admin/users/{item['id']}/preview/dashboard")
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["user"]["telegram_username"] == "runner_target"
    assert "stats" in preview_payload

    runs = client.get(f"/api/admin/users/{item['id']}/preview/runs")
    assert runs.status_code == 200
    assert isinstance(runs.json(), list)

    best_results = client.get(f"/api/admin/users/{item['id']}/preview/runs/best-results")
    assert best_results.status_code == 200
    assert isinstance(best_results.json(), list)

    personal_records = client.get(f"/api/admin/users/{item['id']}/preview/runs/personal-records")
    assert personal_records.status_code == 200
    assert isinstance(personal_records.json(), list)

    role_stats = client.get(f"/api/admin/users/{item['id']}/preview/volunteering/role-stats")
    assert role_stats.status_code == 200
    assert isinstance(role_stats.json(), list)

    visited_map = client.get(f"/api/admin/users/{item['id']}/preview/locations/visited/map")
    assert visited_map.status_code == 200
    assert "points" in visited_map.json()


def test_admin_users_pagination(client: TestClient, db_session: Session) -> None:
    for index in range(3):
        db_session.add(
            User(
                telegram_id=int(uuid4().int % 10_000_000_000) + index,
                telegram_username=f"paginated_user_{index}",
                consent_accepted=True,
            )
        )
    db_session.commit()

    _login_admin(client)

    first_page = client.get("/api/admin/users", params={"limit": 2, "offset": 0})
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert first_payload["limit"] == 2
    assert first_payload["offset"] == 0
    assert len(first_payload["items"]) == 2
    assert first_payload["total"] >= 3

    second_page = client.get("/api/admin/users", params={"limit": 2, "offset": 2})
    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert len(second_payload["items"]) >= 1
    first_ids = {item["id"] for item in first_payload["items"]}
    second_ids = {item["id"] for item in second_payload["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_admin_users_lists_oauth_only_user(client: TestClient, db_session: Session) -> None:
    oauth_user = User(telegram_id=None, display_name="VK Admin Test", consent_accepted=True)
    db_session.add(oauth_user)
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=oauth_user.id,
            provider=AuthProvider.yandex,
            external_id="yandex-1",
            display_name="Dmitry",
            email="popov.dmitii@yandex.ru",
            profile_json={},
        )
    )
    db_session.commit()

    _login_admin(client)

    list_response = client.get("/api/admin/users", params={"q": "popov.dmitii@yandex.ru"})
    assert list_response.status_code == 200
    payload = list_response.json()
    item = next(row for row in payload["items"] if row["id"] == str(oauth_user.id))
    assert item["telegram_id"] is None
    assert any(login["provider"] == "yandex" for login in item["auth_logins"])


def test_admin_users_search_by_platform_participant_name(client: TestClient, db_session: Session) -> None:
    target = User(
        telegram_id=int(uuid4().int % 10_000_000_000),
        telegram_username="other_account",
        display_name="Совсем другое имя",
        consent_accepted=True,
    )
    db_session.add(target)
    db_session.flush()

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    external_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_id,
        display_name="Кузин Олег",
        profile_url=f"https://5verst.ru/userstats/{external_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=target.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_id,
            external_url=f"https://5verst.ru/userstats/{external_id}/",
        )
    )
    db_session.commit()

    _login_admin(client)

    list_response = client.get("/api/admin/users", params={"q": "Кузин"})
    assert list_response.status_code == 200
    payload = list_response.json()
    item = next(row for row in payload["items"] if row["id"] == str(target.id))
    link = next(link for link in item["platform_links"] if link["platform_code"] == "five_verst")
    assert link["display_name"] == "Кузин Олег"
    assert link["external_user_id"] == external_id
