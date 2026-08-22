"""HTTP-обёртки над /api/internal/bot/admin/* — вызовы админ-команд бота.

Перенесено из vk_bot/pipeline.py при переводе admin-бота с ВК на Telegram.
"""

from __future__ import annotations

import httpx

from app.services.admin_report_format import format_admin_stats, format_pipeline_status
from bot_app.settings import BotSettings, bot_headers


def _base_url(settings: BotSettings) -> str:
    return settings.api_base_url.rstrip("/")


def fetch_stats(settings: BotSettings, period_days: int) -> str:
    response = httpx.get(
        f"{_base_url(settings)}/api/internal/bot/admin/stats",
        params={"telegram_id": settings.admin_telegram_id, "period_days": period_days},
        headers=bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"stats API {response.status_code}: {detail}")
    return format_admin_stats(response.json())


def fetch_pipeline_status(settings: BotSettings) -> str:
    response = httpx.get(
        f"{_base_url(settings)}/api/internal/bot/admin/sync-status",
        params={"telegram_id": settings.admin_telegram_id},
        headers=bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-status API {response.status_code}: {detail}")
    return format_pipeline_status(response.json())


def fetch_sweep_status(settings: BotSettings) -> str:
    response = httpx.get(
        f"{_base_url(settings)}/api/internal/bot/admin/sweep-status",
        params={"telegram_id": settings.admin_telegram_id},
        headers=bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sweep-status API {response.status_code}: {detail}")
    return response.json()["text"]


def list_pipelines(settings: BotSettings) -> list[tuple[str, str]]:
    response = httpx.get(
        f"{_base_url(settings)}/api/internal/bot/admin/sync-pipelines",
        params={"telegram_id": settings.admin_telegram_id},
        headers=bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-pipelines API {response.status_code}: {detail}")
    return [(item["key"], item["label"]) for item in response.json()]


def enqueue_pipeline(settings: BotSettings, name: str, *, location_slug: str | None = None) -> str:
    response = httpx.post(
        f"{_base_url(settings)}/api/internal/bot/admin/sync-enqueue",
        params={"telegram_id": settings.admin_telegram_id},
        json={"pipeline": name, "location_slug": location_slug},
        headers=bot_headers(settings),
        timeout=30.0,
    )
    if response.status_code == 400:
        raise ValueError(response.json().get("detail", "Неизвестный пайплайн"))
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-enqueue API {response.status_code}: {detail}")
    return response.json()["message"]


def sync_protocol_url(settings: BotSettings, url: str) -> str:
    response = httpx.post(
        f"{_base_url(settings)}/api/internal/bot/admin/sync-protocol",
        params={"telegram_id": settings.admin_telegram_id},
        json={"url": url},
        headers=bot_headers(settings),
        timeout=120.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-protocol API {response.status_code}: {detail}")
    data = response.json()
    platform = data.get("platform", "?")
    location = data.get("location_slug", "?")
    event_date = data.get("event_date", "?")
    runs = data.get("run_results_upserted", 0)
    vols = data.get("volunteer_results_upserted", 0)
    changed = "да" if data.get("protocol_changed") else "нет"
    return (
        f"✅ Протокол обновлён ({platform})\n"
        f"Локация: {location}\n"
        f"Дата: {event_date}\n"
        f"Пробежек: {runs}, волонтёров: {vols}\n"
        f"Протокол изменился: {changed}"
    )
