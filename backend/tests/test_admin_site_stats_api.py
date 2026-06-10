from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.site_stats import record_pageview
from app.db.session import get_db
from app.main import app


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
        mp.setattr("app.core.site_stats.get_redis", lambda: fake_redis)
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


def test_record_pageview_increments_counters(
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.site_stats.get_redis", lambda: fake_redis)
    record_pageview("/demo", authenticated=False, visitor_key="a:anon-11111111")
    record_pageview(
        "/dashboard",
        authenticated=True,
        visitor_key="u:5083e9d8-1cd8-4ce3-a937-2b95274f6d87",
    )
    record_pageview("/dashboard", authenticated=True, visitor_key="u:5083e9d8-1cd8-4ce3-a937-2b95274f6d87")

    today = datetime.now(timezone.utc).date().isoformat()
    assert int(fake_redis.get(f"stats:day:{today}:pv:total") or 0) == 3
    assert int(fake_redis.get(f"stats:day:{today}:pv:demo") or 0) == 1
    assert int(fake_redis.get(f"stats:day:{today}:pv:app") or 0) == 2
    assert fake_redis.pfcount(f"stats:day:{today}:uv") == 2


def test_record_pageview_rejects_invalid_visitor_key(
    fake_redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.site_stats.get_redis", lambda: fake_redis)
    record_pageview("/", authenticated=False, visitor_key="invalid")
    today = datetime.now(timezone.utc).date().isoformat()
    assert fake_redis.pfcount(f"stats:day:{today}:uv") == 0


def test_admin_stats_requires_admin(client: TestClient) -> None:
    response = client.get("/api/admin/stats")
    assert response.status_code == 401


def test_admin_stats_for_admin(admin_client: TestClient) -> None:
    response = admin_client.get("/api/admin/stats?period_days=30")
    assert response.status_code == 200
    payload = response.json()
    assert "overview" in payload
    assert "users_total" in payload["overview"]
    assert "pageviews_period" in payload["overview"]
    assert "users_news_subscribed" not in payload["overview"]
    assert len(payload["users_new_by_day"]) == 30
    assert len(payload["pageviews_by_day"]) == 30
    assert len(payload["logins_by_day"]) == 30
    assert "logins_by_day_db" not in payload


def test_admin_stats_period_one_day(admin_client: TestClient) -> None:
    response = admin_client.get("/api/admin/stats?period_days=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["period_days"] == 1
    assert len(payload["users_new_by_day"]) == 1
    assert len(payload["pageviews_by_day"]) == 1


def test_admin_stats_rejects_zero_period(admin_client: TestClient) -> None:
    response = admin_client.get("/api/admin/stats?period_days=0")
    assert response.status_code == 422


def test_pageview_endpoint(client: TestClient, fake_redis: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.rate_limit.get_redis_client", lambda: fake_redis)
    monkeypatch.setattr("app.core.site_stats.get_redis", lambda: fake_redis)
    response = client.post(
        "/api/stats/pageview",
        json={"path": "/", "authenticated": False, "visitor_key": "a:test-visitor-1"},
    )
    assert response.status_code == 204
    today = datetime.now(timezone.utc).date().isoformat()
    assert fake_redis.pfcount(f"stats:day:{today}:uv") == 1
