from __future__ import annotations

import httpx

from vk_bot.settings import VkBotSettings, vk_bot_headers


def _base_url(settings: VkBotSettings) -> str:
    return settings.api_base_url.rstrip("/")


def list_pipelines(settings: VkBotSettings) -> list[tuple[str, str]]:
    """Список (key, label) доступных пайплайнов из internal API."""
    response = httpx.get(
        f"{_base_url(settings)}/api/internal/vk-bot/sync-pipelines",
        params={"vk_user_id": settings.vk_admin_user_id},
        headers=vk_bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-pipelines API {response.status_code}: {detail}")
    return [(item["key"], item["label"]) for item in response.json()]


def enqueue_pipeline(
    settings: VkBotSettings, name: str, *, location_slug: str | None = None
) -> str:
    """Поставить пайплайн в очередь через internal API.

    ValueError — пайплайн неизвестен / не указан slug (HTTP 400),
    текст приходит с сервера и показывается пользователю как есть.
    """
    response = httpx.post(
        f"{_base_url(settings)}/api/internal/vk-bot/sync-enqueue",
        params={"vk_user_id": settings.vk_admin_user_id},
        json={"pipeline": name, "location_slug": location_slug},
        headers=vk_bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code == 400:
        raise ValueError(response.json().get("detail", "Неизвестный пайплайн"))
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-enqueue API {response.status_code}: {detail}")
    return response.json()["message"]
