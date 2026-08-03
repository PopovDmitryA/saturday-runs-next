"""Заявки на координаты локаций: диалог с админом идёт в Telegram, а не в ВК."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Location, Platform
from app.services import location_coordinate_service as module

ADMIN_CHAT_ID = 4242


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        app_secret_key="test-secret-key",
        telegram_bot_token="test-token",
        telegram_admin_chat_id=ADMIN_CHAT_ID,
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Подменяем Telegram-канал: копим тексты и раздаём предсказуемые message_id."""
    texts: list[str] = []

    def _send(text: str, *, reply_to_message_id: int | None = None) -> tuple[int, int]:
        texts.append(text)
        return ADMIN_CHAT_ID, 100 + len(texts)

    monkeypatch.setattr(module, "notify_admin_dialog", _send)
    monkeypatch.setattr(module, "admin_dialog_chat_id", lambda: ADMIN_CHAT_ID)
    return texts


@pytest.fixture
def location(db_session: Session) -> Location:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")
    row = Location(
        platform_id=platform.id,
        external_key=f"testpark-{uuid4().hex[:8]}",
        name="Парк без координат",
        source_url="https://s95.ru/testpark",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_coordinate_dialog_full_flow_over_telegram(
    db_session: Session,
    location: Location,
    admin_settings: Settings,
    sent_messages: list[str],
) -> None:
    request = module.maybe_request_coordinates_for_new_location(db_session, location, is_new=True)
    assert request is not None
    assert request.admin_telegram_chat_id == ADMIN_CHAT_ID
    assert request.request_telegram_message_id == 101
    assert "без координат" in sent_messages[0]

    reply = module.handle_admin_coordinate_message(
        db_session, ADMIN_CHAT_ID, "55.703:37.623", reply_to_message_id=101
    )
    assert reply is not None
    assert "Принято" in reply
    assert request.status == "awaiting_confirmation"
    assert request.verify_telegram_message_id == 102

    confirmation = module.handle_admin_coordinate_message(
        db_session, ADMIN_CHAT_ID, "ок", reply_to_message_id=102
    )
    assert confirmation is not None
    assert "Координаты применены" in confirmation
    assert location.latitude == pytest.approx(55.703)
    assert location.longitude == pytest.approx(37.623)


def test_coordinate_dialog_ignores_other_chats(admin_settings: Settings) -> None:
    assert (
        module.handle_admin_coordinate_message(
            None, ADMIN_CHAT_ID + 1, "55.7:37.6", reply_to_message_id=101
        )
        is None
    )


def test_coordinate_dialog_passes_through_foreign_replies(
    db_session: Session, admin_settings: Settings
) -> None:
    """Ответ админа на любое другое сообщение бота — не наша забота."""
    assert (
        module.handle_admin_coordinate_message(
            db_session, ADMIN_CHAT_ID, "текст рассылки", reply_to_message_id=999_999
        )
        is None
    )


def test_coordinate_dialog_hints_when_reply_missing(admin_settings: Settings) -> None:
    reply = module.handle_admin_coordinate_message(
        None, ADMIN_CHAT_ID, "55.7:37.6", reply_to_message_id=None
    )
    assert reply == module.REPLY_REQUIRED_HINT
