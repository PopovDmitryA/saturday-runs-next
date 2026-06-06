from __future__ import annotations

import logging
import random
from typing import Any

import httpx

from vk_bot.settings import get_vk_bot_settings

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.199"


class VkApiError(RuntimeError):
    pass


def _api(method: str, **params: Any) -> Any:
    settings = get_vk_bot_settings()
    payload = {
        "access_token": settings.vk_bot_group_token,
        "v": VK_API_VERSION,
        **params,
    }
    response = httpx.post(f"https://api.vk.com/method/{method}", data=payload, timeout=60.0)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise VkApiError(str(body["error"]))
    return body["response"]


def send_message(peer_id: int, text: str, *, reply_to: int | None = None) -> int | None:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "message": text[:4096],
        "random_id": random.randint(1, 2_000_000_000),
    }
    if reply_to is not None:
        params["reply_to"] = reply_to
    message_id = _api("messages.send", **params)
    return int(message_id) if message_id is not None else None


class VkLongPoll:
    def __init__(self) -> None:
        self._server: str | None = None
        self._key: str | None = None
        self._ts: str | None = None

    def _ensure_server(self) -> None:
        settings = get_vk_bot_settings()
        if not settings.vk_bot_group_id:
            raise VkApiError("VK_BOT_GROUP_ID is not set")
        response = _api("groups.getLongPollServer", group_id=abs(settings.vk_bot_group_id))
        self._server = str(response["server"])
        self._key = str(response["key"])
        self._ts = str(response["ts"])

    def poll(self) -> list[dict[str, Any]]:
        if not self._server or not self._key or not self._ts:
            self._ensure_server()
        assert self._server and self._key and self._ts
        url = f"{self._server}?act=a_check&key={self._key}&ts={self._ts}&wait=25"
        response = httpx.get(url, timeout=60.0)
        response.raise_for_status()
        data = response.json()
        if data.get("failed"):
            self._ensure_server()
            return []
        self._ts = str(data["ts"])
        updates = data.get("updates") or []
        parsed: list[dict[str, Any]] = []
        for item in updates:
            if isinstance(item, dict):
                parsed.append(item)
            elif isinstance(item, list) and item and item[0] == 4:
                parsed.append(
                    {
                        "type": "message_new",
                        "object": {
                            "message": {
                                "id": item[1],
                                "peer_id": item[3],
                                "text": item[5] if len(item) > 5 else "",
                                "from_id": item[3],
                            }
                        },
                    }
                )
        return parsed
