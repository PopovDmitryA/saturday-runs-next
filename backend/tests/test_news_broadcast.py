from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import User
from app.services.broadcast_compose_state import (
    clear_broadcast_state,
    get_broadcast_draft,
    is_awaiting_broadcast_text,
    save_broadcast_draft,
    start_broadcast_compose,
)
from app.services.news_broadcast_service import send_news_broadcast


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def bot_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_token="test-token",
        admin_telegram_id=9001,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(db_session: Session, fake_redis: fakeredis.FakeRedis, bot_settings: Settings) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: bot_settings

    with patch("app.core.redis_client.get_redis_client", return_value=fake_redis):
        with patch("app.services.broadcast_compose_state.get_redis_client", return_value=fake_redis):
            with TestClient(app) as test_client:
                yield test_client

    app.dependency_overrides.clear()


def test_broadcast_compose_state(fake_redis: fakeredis.FakeRedis) -> None:
    start_broadcast_compose(fake_redis, 9001)
    assert is_awaiting_broadcast_text(fake_redis, 9001) is True
    save_broadcast_draft(fake_redis, 9001, "Hello")
    assert is_awaiting_broadcast_text(fake_redis, 9001) is False
    assert get_broadcast_draft(fake_redis, 9001) == "Hello"
    clear_broadcast_state(fake_redis, 9001)
    assert get_broadcast_draft(fake_redis, 9001) is None


def test_broadcast_api_flow(client: TestClient, db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    telegram_id = int(uuid4().int % 10_000_000_000)
    user = User(
        telegram_id=telegram_id,
        telegram_chat_id=telegram_id,
        telegram_username=f"subscriber_{telegram_id}",
        news_subscribed=True,
    )
    db_session.add(user)
    db_session.flush()

    headers = {"X-Bot-Secret": "bot-secret"}

    try:
        denied = client.post("/api/internal/bot/broadcast/start", json={"telegram_id": 1}, headers=headers)
        assert denied.status_code == 403

        start = client.post("/api/internal/bot/broadcast/start", json={"telegram_id": 9001}, headers=headers)
        assert start.status_code == 200
        start_payload = start.json()
        assert start_payload["count"] >= 1
        assert any(item["telegram_id"] == telegram_id for item in start_payload["subscribers"])

        list_response = client.get(
            "/api/internal/bot/broadcast/subscribers",
            params={"telegram_id": 9001},
            headers=headers,
        )
        assert list_response.status_code == 200
        assert any(item["telegram_id"] == telegram_id for item in list_response.json()["subscribers"])

        draft = client.post(
            "/api/internal/bot/broadcast/draft",
            json={"telegram_id": 9001, "message": "News update"},
            headers=headers,
        )
        assert draft.status_code == 200
        assert draft.json()["message"] == "News update"

        with patch("app.services.news_broadcast_service.send_telegram_broadcast_message", return_value=(True, "ok")):
            send = client.post("/api/internal/bot/broadcast/send", json={"telegram_id": 9001}, headers=headers)
        assert send.status_code == 200
        payload = send.json()
        assert payload["recipients"] >= 1
        assert payload["sent"] >= 1
        assert payload["failed"] == 0
        assert get_broadcast_draft(fake_redis, 9001) is None
    finally:
        db_session.delete(user)
        db_session.commit()


def test_send_news_broadcast_skips_empty_message(db_session: Session) -> None:
    with pytest.raises(ValueError, match="empty"):
        send_news_broadcast(db_session, "   ")
