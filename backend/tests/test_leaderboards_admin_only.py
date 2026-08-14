"""Закрытые рейтинги: до открытия их видит только админ.

«Открытия» ждут ручной разметки С95 (решение Дмитрия 14.08.2026), поэтому
метрика ведёт себя для публики как несуществующая — не 403, а 404: о ещё не
опубликованном рейтинге не должно быть видно даже того, что он есть.
"""

from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.services.leaderboard_service import ADMIN_ONLY_METRICS, LEADERBOARD_METRICS

CLOSED_METRIC = "openings"
OPEN_METRIC = "runs"


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
        mp.setattr("app.services.auth_service.check_rate_limit", lambda *_args, **_kwargs: True)
        with TestClient(app) as test_client:
            yield test_client

    app.dependency_overrides.clear()


def _login(client: TestClient, telegram_id: int) -> None:
    login_response = client.post("/api/auth/login-request")
    assert login_response.status_code == 200
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": login_response.json()["request_token"],
            "telegram_id": telegram_id,
            "telegram_username": f"user{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200
    token = confirm_response.json()["magic_link"].split("token=")[-1]
    callback = client.get(f"/api/auth/callback?token={token}", follow_redirects=False)
    assert callback.status_code == 302


def test_closed_metric_is_registered_but_hidden() -> None:
    assert CLOSED_METRIC in LEADERBOARD_METRICS
    assert set(ADMIN_ONLY_METRICS) <= set(LEADERBOARD_METRICS)


def test_closed_metric_hidden_from_anonymous(client: TestClient) -> None:
    assert client.get(f"/api/leaderboards/{CLOSED_METRIC}?limit=1").status_code == 404
    # Открытый рейтинг анониму по-прежнему доступен — витрина публичная.
    assert client.get(f"/api/leaderboards/{OPEN_METRIC}?limit=1").status_code == 200


def test_closed_metric_hidden_from_ordinary_user(client: TestClient) -> None:
    _login(client, telegram_id=4242)
    assert client.get(f"/api/leaderboards/{CLOSED_METRIC}?limit=1").status_code == 404
    # И своя строка тоже: залогиненность сама по себе доступа не даёт.
    assert client.get(f"/api/leaderboards/{CLOSED_METRIC}/me").status_code == 404


def test_closed_metric_visible_to_admin(client: TestClient) -> None:
    _login(client, telegram_id=9001)
    response = client.get(f"/api/leaderboards/{CLOSED_METRIC}?limit=1")
    assert response.status_code == 200
    assert response.json()["metric"] == CLOSED_METRIC
    assert client.get(f"/api/leaderboards/{CLOSED_METRIC}/me").status_code == 200
