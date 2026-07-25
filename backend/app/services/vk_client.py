"""Низкоуровневый клиент VK API.

Fallback-канал для admin-уведомлений (`app/services/admin_telegram_notify.py` →
`vk_admin_notify.py`) — используется только если Telegram-прокси недоступна.
"""

from __future__ import annotations

import logging
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"
VK_MESSAGE_LIMIT = 4096
VK_API_BASE_URL = "https://api.vk.com/method"


class VkApiError(RuntimeError):
    """VK вернул `error` в теле ответа."""


def call_vk_api(method: str, token: str, *, timeout: float = 60.0, **params: Any) -> Any:
    payload = {"access_token": token, "v": VK_API_VERSION, **params}
    response = httpx.post(f"{VK_API_BASE_URL}/{method}", data=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise VkApiError(str(body["error"]))
    return body.get("response")


def send_vk_message(
    token: str,
    peer_id: int,
    text: str,
    *,
    reply_to: int | None = None,
    timeout: float = 60.0,
) -> int | None:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "message": text[:VK_MESSAGE_LIMIT],
        "random_id": random.randint(1, 2_000_000_000),
    }
    if reply_to is not None:
        params["reply_to"] = reply_to
    message_id = call_vk_api("messages.send", token, timeout=timeout, **params)
    return int(message_id) if message_id is not None else None
