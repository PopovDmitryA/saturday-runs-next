from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.vk_client import VkApiError, call_vk_api, send_vk_message
from vk_bot.settings import get_vk_bot_settings

logger = logging.getLogger(__name__)

__all__ = ["VkApiError", "VkLongPoll", "send_message"]


def send_message(peer_id: int, text: str, *, reply_to: int | None = None) -> int | None:
    settings = get_vk_bot_settings()
    return send_vk_message(
        settings.vk_bot_group_token, peer_id, text, reply_to=reply_to
    )


class VkLongPoll:
    def __init__(self) -> None:
        self._server: str | None = None
        self._key: str | None = None
        self._ts: str | None = None

    def _request_server(self, *, keep_ts: bool) -> None:
        """Запросить параметры Long Poll сервера.

        ``keep_ts`` сохраняет текущий ``ts`` (failed:2 — протух только key),
        иначе берём ``ts`` из ответа (первый запуск / failed:3).
        """
        settings = get_vk_bot_settings()
        if not settings.vk_bot_group_id:
            raise VkApiError("VK_BOT_GROUP_ID is not set")
        response = call_vk_api(
            "groups.getLongPollServer",
            settings.vk_bot_group_token,
            group_id=abs(settings.vk_bot_group_id),
        )
        self._server = str(response["server"])
        self._key = str(response["key"])
        if not keep_ts or self._ts is None:
            self._ts = str(response["ts"])

    def poll(self) -> list[dict[str, Any]]:
        if not self._server or not self._key or not self._ts:
            self._request_server(keep_ts=False)
        assert self._server and self._key and self._ts
        url = f"{self._server}?act=a_check&key={self._key}&ts={self._ts}&wait=25"
        response = httpx.get(url, timeout=60.0)
        response.raise_for_status()
        data = response.json()

        failed = data.get("failed")
        if failed:
            # 1 — устарел ts: берём новый из ответа, сервер/key валидны.
            # 2 — протух key: перезапрашиваем сервер, ts сохраняем.
            # 3 (и прочее) — потеряна история: полный сброс.
            if failed == 1 and data.get("ts") is not None:
                self._ts = str(data["ts"])
            elif failed == 2:
                self._request_server(keep_ts=True)
            else:
                self._request_server(keep_ts=False)
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
