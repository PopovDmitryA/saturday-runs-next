from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BroadcastRecipient:
    user_id: str
    telegram_id: int
    telegram_chat_id: int
    telegram_username: str | None
    display_name: str | None
    label: str


@dataclass(frozen=True)
class BroadcastDeliveryFailure:
    telegram_id: int
    label: str
    error: str


@dataclass(frozen=True)
class BroadcastSendResult:
    recipients: int
    sent: int
    failed: int
    failures: list[BroadcastDeliveryFailure]


def list_news_subscribers(db: Session) -> list[BroadcastRecipient]:
    users = (
        db.query(User)
        .filter(User.news_subscribed.is_(True), User.telegram_chat_id.isnot(None))
        .order_by(User.telegram_id)
        .all()
    )
    recipients: list[BroadcastRecipient] = []
    for user in users:
        if user.telegram_chat_id is None:
            continue
        label = user.telegram_username or user.display_name or str(user.telegram_id)
        recipients.append(
            BroadcastRecipient(
                user_id=str(user.id),
                telegram_id=user.telegram_id,
                telegram_chat_id=user.telegram_chat_id,
                telegram_username=user.telegram_username,
                display_name=user.display_name,
                label=label,
            )
        )
    return recipients


def send_telegram_broadcast_message(
    *,
    token: str,
    chat_id: int,
    text: str,
) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        return False, str(exc)
    if response.status_code != 200:
        detail = response.text
        try:
            detail = response.json().get("description", detail)
        except Exception:
            pass
        return False, detail
    return True, "ok"


def send_news_broadcast(
    db: Session,
    message: str,
    *,
    settings: Settings | None = None,
    delay_seconds: float = 0.05,
) -> BroadcastSendResult:
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    text = message.strip()
    if not text:
        raise ValueError("Broadcast message is empty")

    subscribers = list_news_subscribers(db)
    sent = 0
    failures: list[BroadcastDeliveryFailure] = []

    for recipient in subscribers:
        ok, detail = send_telegram_broadcast_message(
            token=settings.telegram_bot_token,
            chat_id=recipient.telegram_chat_id,
            text=text,
        )
        if ok:
            sent += 1
        else:
            failures.append(
                BroadcastDeliveryFailure(
                    telegram_id=recipient.telegram_id,
                    label=recipient.label,
                    error=detail,
                )
            )
            logger.warning(
                "Broadcast failed for %s (%s): %s",
                recipient.label,
                recipient.telegram_id,
                detail,
            )
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return BroadcastSendResult(
        recipients=len(subscribers),
        sent=sent,
        failed=len(failures),
        failures=failures,
    )
