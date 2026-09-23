"""HTTP-слой уведомлений: настройки, баннер, страница отписки, бот, колокольчик, админка."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import AuthIdentity, AuthProvider, BacklogCardType, User
from app.services import notification_channels_service as channels
from app.services import notification_service as notify
from app.services.backlog_service import create_card
from app.services.notification_channels_service import CheckOutcome

SECRET = "test-secret-key"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_secret_key=SECRET,
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        telegram_bot_token="t",
        smtp_enabled=True,
        smtp_host="smtp.test",
        smtp_user="support@run5k.test",
        smtp_password="p",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def user(db_session: Session) -> User:
    row = User(display_name="Бегун", telegram_id=4242, telegram_chat_id=4242, telegram_username="runner")
    db_session.add(row)
    db_session.flush()
    db_session.add(
        AuthIdentity(
            user_id=row.id, provider=AuthProvider.email, external_id="runner@example.com", email="runner@example.com"
        )
    )
    db_session.commit()
    return row


@pytest.fixture
def client(
    db_session: Session, settings: Settings, user: User, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_admin_user] = lambda: user
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(channels, "get_settings", lambda: settings)
    monkeypatch.setattr(notify, "enqueue_delivery", lambda delivery_id: True)
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda t, s: CheckOutcome(ok=True))
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_settings_state_and_channel_toggles(client: TestClient) -> None:
    state = client.get("/api/settings/notifications").json()
    assert state["enabled"] is False
    by_channel = {c["channel"]: c for c in state["channels"]}
    assert by_channel["telegram"]["linked"] and by_channel["telegram"]["deliverable"] is True
    assert by_channel["telegram"]["enabled"] is False
    assert by_channel["email"]["label"] == "runner@example.com" and by_channel["email"]["email_source"] == "email"
    assert by_channel["vk"]["available"] is False

    state = client.put("/api/settings/notifications/channels/telegram", json={"enabled": True}).json()
    assert state["enabled"] is True
    assert {c["channel"]: c["enabled"] for c in state["channels"]}["telegram"] is True
    assert client.put("/api/settings/notifications/channels/vk", json={"enabled": True}).status_code == 400

    state = client.put(
        "/api/settings/notifications", json={"kinds": {"runs": False}, "primary_channel": "email"}
    ).json()
    assert {k["code"]: k["enabled"] for k in state["kinds"]}["runs"] is False
    assert state["primary_channel"] == "email"
    assert client.put("/api/settings/notifications", json={"primary_channel": "fax"}).status_code == 400
    assert client.put("/api/settings/notifications", json={"kinds": {"volunteer_signup": True}}).status_code == 400


def test_channel_check_reports_problem(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda t, s: CheckOutcome(ok=False, error="telegram 403"))
    response = client.post("/api/settings/notifications/channels/telegram/check")
    assert response.status_code == 200 and response.json() == {"ok": False, "error": "telegram 403"}
    assert client.post("/api/settings/notifications/channels/pigeon/check").status_code == 404
    state = client.get("/api/settings/notifications").json()
    telegram = next(c for c in state["channels"] if c["channel"] == "telegram")
    assert telegram["deliverable"] is False and "Start" in telegram["problem"]


def test_nudge_enable_and_dismiss(client: TestClient) -> None:
    assert client.get("/api/settings/notifications/nudge").json()["show"] is True
    enabled = client.post("/api/settings/notifications/enable").json()
    assert enabled["channel"] == "telegram" and enabled["state"]["enabled"] is True
    assert client.get("/api/settings/notifications/nudge").json()["show"] is False

    client.put("/api/settings/notifications/channels/telegram", json={"enabled": False})
    assert client.get("/api/settings/notifications/nudge").json()["show"] is True
    assert client.post("/api/settings/notifications/nudge/dismiss").json()["show"] is False


def test_telegram_connect_link_and_bot_confirm(client: TestClient, db_session: Session, user: User) -> None:
    user.telegram_chat_id = None
    user.telegram_id = None
    db_session.commit()
    response = client.post("/api/settings/notifications/channels/telegram/connect")
    assert response.status_code == 200
    url = response.json()["connect_url"]
    assert url.startswith("https://t.me/TestBot?start=notify_")
    token = url.rsplit("notify_", 1)[1]

    denied = client.post("/api/auth/bot/notify-confirm", json={"token": token, "telegram_id": 1, "telegram_chat_id": 1})
    assert denied.status_code == 403
    stale = client.post(
        "/api/auth/bot/notify-confirm",
        json={"token": "nope", "telegram_id": 1, "telegram_chat_id": 1},
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert stale.status_code == 200 and stale.json()["ok"] is False
    confirmed = client.post(
        "/api/auth/bot/notify-confirm",
        json={"token": token, "telegram_id": 4242, "telegram_chat_id": 4242},
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirmed.status_code == 200 and confirmed.json()["ok"] is True
    state = client.get("/api/settings/notifications").json()
    assert state["enabled"] is True


def test_test_message_requires_enabled_channel(client: TestClient) -> None:
    assert client.post("/api/settings/notifications/test").status_code == 400
    client.put("/api/settings/notifications/channels/telegram", json={"enabled": True})
    response = client.post("/api/settings/notifications/test")
    assert response.status_code == 200 and response.json() == {"queued": True, "channels": ["telegram"]}
    assert client.post("/api/settings/notifications/test").json()["queued"] is False


def test_unsubscribe_page(client: TestClient, db_session: Session, user: User) -> None:
    notify.set_channel_enabled(db_session, user, "telegram", True)
    db_session.commit()

    broken = client.get("/api/notifications/unsubscribe", params={"token": "garbage"})
    assert broken.status_code == 200 and "Ссылка не сработала" in broken.text

    page = client.get(
        "/api/notifications/unsubscribe", params={"token": notify.make_unsubscribe_token(user.id, "runs", SECRET)}
    )
    assert page.status_code == 200 and "«Мои пробежки» выключены" in page.text
    assert "Выключить все уведомления" in page.text and "/settings#notifications" in page.text
    prefs = notify.get_prefs(db_session, user.id)
    assert prefs is not None and prefs.kinds == {"runs": False} and notify.is_enabled(db_session, user.id)

    everything = client.get(
        "/api/notifications/unsubscribe",
        params={"token": notify.make_unsubscribe_token(user.id, notify.UNSUBSCRIBE_ALL, SECRET)},
    )
    assert "Уведомления выключены" in everything.text
    assert notify.is_enabled(db_session, user.id) is False


def test_backlog_subscription_route(client: TestClient, db_session: Session, user: User) -> None:
    author = User(display_name="Автор")
    db_session.add(author)
    db_session.commit()
    card = create_card(
        db_session,
        author_id=author.id,
        type_=BacklogCardType.feature,
        category="other",
        title="Идея",
        description="Описание",
        is_anonymous=False,
    )
    listed = client.get("/api/backlog/cards").json()["items"]
    assert {c["id"]: c["is_subscribed"] for c in listed}[str(card.id)] is False
    followed = client.put(f"/api/backlog/cards/{card.id}/subscription", json={"subscribed": True}).json()
    assert followed["is_subscribed"] is True
    unfollowed = client.put(f"/api/backlog/cards/{card.id}/subscription", json={"subscribed": False}).json()
    assert unfollowed["is_subscribed"] is False


def test_admin_notifications_journal(client: TestClient, db_session: Session, user: User) -> None:
    notify.set_channel_enabled(db_session, user, "telegram", True)
    db_session.commit()
    notify.notify_user(db_session, user, "runs", title="🏃 Пробежка", text="x", dedupe_key="a")
    notify.notify_user(db_session, user, "backlog", title="💬 Комментарий", text="x", dedupe_key="b")

    response = client.get("/api/admin/notifications", params={"period_days": 7, "limit": 10})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2 and data["items_total"] == 2
    assert {row["key"]: row["count"] for row in data["by_kind"]} == {"runs": 1, "backlog": 1}
    assert {row["key"]: row["count"] for row in data["subscribers_by_channel"]} == {"telegram": 1}
    assert data["items"][0]["user_label"] == "@runner" and data["items"][0]["status"] == "queued"

    filtered = client.get("/api/admin/notifications", params={"kind": "runs"}).json()
    assert filtered["items_total"] == 1 and filtered["items"][0]["title"] == "🏃 Пробежка"
    assert client.get("/api/admin/notifications", params={"status": "bogus"}).status_code == 422
