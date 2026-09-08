from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.workers.celery_app import celery_app


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
def client(db_session: Session, admin_settings: Settings) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: admin_settings
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.auth_service.check_rate_limit", lambda *_args, **_kwargs: True)
        with TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(client: TestClient, admin_settings: Settings) -> TestClient:
    telegram_id = admin_settings.admin_telegram_id
    request_token = client.post("/api/auth/login-request").json()["request_token"]
    confirm = client.post(
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
    assert confirm.status_code == 200
    token = confirm.json()["magic_link"].split("token=")[-1]
    assert client.get(f"/api/auth/callback?token={token}", follow_redirects=False).status_code == 302
    return client


def test_create_and_read_resync_request(admin_client: TestClient) -> None:
    with patch("app.services.admin_resync_service._dispatch") as dispatch:
        response = admin_client.post(
            "/api/admin/resync",
            json={"url": "https://5verst.ru/serpukhovgorodskoybor/results/22.08.2026/"},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["platform_code"] == "five_verst"
    assert body["kind"] == "protocol"
    assert body["status"] == "queued"
    assert body["target"]["slug"] == "serpukhovgorodskoybor"
    assert body["steps"][0]["code"] == "queued"
    assert dispatch.call_count == 1

    single = admin_client.get(f"/api/admin/resync/{body['id']}")
    assert single.status_code == 200
    assert single.json()["id"] == body["id"]

    listing = admin_client.get("/api/admin/resync")
    assert listing.status_code == 200
    assert any(item["id"] == body["id"] for item in listing.json()["items"])


def test_create_resync_rejects_bad_url(admin_client: TestClient) -> None:
    response = admin_client.post("/api/admin/resync", json={"url": "https://example.com/whatever"})
    assert response.status_code == 400
    assert "5verst" in response.json()["detail"]


def test_resync_requires_admin(client: TestClient) -> None:
    response = client.post("/api/admin/resync", json={"url": "https://5verst.ru/zil/results/all/"})
    assert response.status_code in (401, 403)


def test_event_report_routes_are_gone(admin_client: TestClient) -> None:
    assert admin_client.get("/api/admin/event-report/locations").status_code == 404
    assert admin_client.get("/api/admin/event-report").status_code == 404


def test_tasks_live_in_priority_queues() -> None:
    from app.workers.tasks import admin_resync as tasks

    assert tasks.admin_resync_five_verst_task.queue == "five_verst_user"
    assert tasks.admin_resync_s95_task.queue == "s95_user"
    assert celery_app.tasks["user_sync.admin_resync"].name == tasks.admin_resync_five_verst_task.name
    assert celery_app.conf.task_routes["s95_sync.run_admin_resync"] == {"queue": "s95_user"}
    assert celery_app.conf.task_routes["s95_sync.run_admin_resync"] == {"queue": "s95_user"}
