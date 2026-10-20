from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import fakeredis
import httpx
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
from app.services.news_broadcast_service import (
    BroadcastRecipient,
    send_news_broadcast,
    send_telegram_broadcast_message,
)


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


def test_broadcast_message_goes_through_telegram_proxy() -> None:
    # Напрямую api.telegram.org из РФ недоступен: рассылка обязана идти через
    # ту же прокси, что бот и уведомления (до 21.10.2026 шла мимо неё).
    response = httpx.Response(200, json={"ok": True})
    with patch("app.services.news_broadcast_service.httpx.post", return_value=response) as post:
        ok, detail = send_telegram_broadcast_message(
            token="test-token", chat_id=42, text="Новости", proxy="http://tg-proxy:8118"
        )
    assert (ok, detail) == (True, "ok")
    assert post.call_args.kwargs["proxy"] == "http://tg-proxy:8118"


def test_send_news_broadcast_passes_proxy_from_settings() -> None:
    settings = Settings(
        app_secret_key="test-secret-key",
        telegram_bot_token="test-token",
        telegram_proxy_url="http://tg-proxy:8118",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )
    recipient = BroadcastRecipient(
        user_id=str(uuid4()), telegram_id=1, telegram_chat_id=1, telegram_username=None, display_name=None, label="1"
    )
    with (
        patch("app.services.news_broadcast_service.list_news_subscribers", return_value=[recipient]),
        patch("app.services.news_broadcast_service.send_telegram_broadcast_message", return_value=(True, "ok")) as send,
    ):
        result = send_news_broadcast(MagicMock(spec=Session), "Новости", settings=settings, delay_seconds=0)
    assert result.sent == 1
    assert send.call_args.kwargs["proxy"] == "http://tg-proxy:8118"


def test_send_news_broadcast_skips_empty_message(db_session: Session, bot_settings: Settings) -> None:
    # Токен проверяется раньше текста, поэтому берём тестовые настройки бота.
    with pytest.raises(ValueError, match="empty"):
        send_news_broadcast(db_session, "   ", settings=bot_settings)
