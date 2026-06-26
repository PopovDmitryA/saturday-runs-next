from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.routes import internal_vk_bot
from app.config import Settings, get_settings
from app.main import app

ADMIN_VK_ID = 555
SECRET = "vk-secret"


@pytest.fixture
def vk_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
        vk_admin_user_id=ADMIN_VK_ID,
        vk_bot_internal_secret=SECRET,
    )


@pytest.fixture
def client(vk_settings: Settings) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_settings] = lambda: vk_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_sync_pipelines_requires_secret(client: TestClient) -> None:
    response = client.get("/api/internal/vk-bot/sync-pipelines", params={"vk_user_id": ADMIN_VK_ID})
    assert response.status_code == 403


def test_sync_pipelines_rejects_wrong_secret(client: TestClient) -> None:
    response = client.get(
        "/api/internal/vk-bot/sync-pipelines",
        params={"vk_user_id": ADMIN_VK_ID},
        headers={"X-Bot-Secret": "nope"},
    )
    assert response.status_code == 403


def test_sync_pipelines_rejects_non_admin(client: TestClient) -> None:
    response = client.get(
        "/api/internal/vk-bot/sync-pipelines",
        params={"vk_user_id": 1},
        headers={"X-Bot-Secret": SECRET},
    )
    assert response.status_code == 403


def test_sync_pipelines_returns_list(client: TestClient) -> None:
    response = client.get(
        "/api/internal/vk-bot/sync-pipelines",
        params={"vk_user_id": ADMIN_VK_ID},
        headers={"X-Bot-Secret": SECRET},
    )
    assert response.status_code == 200
    items = response.json()
    keys = {item["key"] for item in items}
    assert "registry" in keys
    assert "s95-latest" in keys
    assert all(item["label"] for item in items)


def test_sync_enqueue_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        internal_vk_bot,
        "enqueue_pipeline",
        lambda name, *, location_slug=None: f"Поставлена в очередь: {name} ({location_slug})",
    )
    response = client.post(
        "/api/internal/vk-bot/sync-enqueue",
        params={"vk_user_id": ADMIN_VK_ID},
        headers={"X-Bot-Secret": SECRET},
        json={"pipeline": "location", "location_slug": "zil"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Поставлена в очередь: location (zil)"


def test_sync_enqueue_unknown_pipeline(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_enqueue(name: str, *, location_slug: str | None = None) -> str:
        raise ValueError("Неизвестный пайплайн. Доступно: latest")

    monkeypatch.setattr(internal_vk_bot, "enqueue_pipeline", fake_enqueue)
    response = client.post(
        "/api/internal/vk-bot/sync-enqueue",
        params={"vk_user_id": ADMIN_VK_ID},
        headers={"X-Bot-Secret": SECRET},
        json={"pipeline": "bogus"},
    )
    assert response.status_code == 400
    assert "Неизвестный пайплайн" in response.json()["detail"]
