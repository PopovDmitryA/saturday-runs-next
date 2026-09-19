"""HTTP-обёртки над /api/internal/bot/admin/* — вызовы админ-команд бота.

Перенесено из vk_bot/pipeline.py при переводе admin-бота с ВК на Telegram.

Запросы асинхронные. Синхронный httpx внутри корутины aiogram останавливает
весь событийный цикл бота на время ответа API: пока админ ждёт /stats или
/sync (секунды, а /sync protocol — до двух минут), бот не подтверждает вход
другим людям и не обновляет свой heartbeat с TTL 90 секунд (BOT-01,
13.09.2026). Остальные модули бота (main.py, broadcast.py) ходят через
AsyncClient — здесь то же самое.
"""

from __future__ import annotations

import httpx

from app.services.admin_report_format import format_admin_stats, format_pipeline_status
from bot_app.settings import BotSettings, bot_headers


def _base_url(settings: BotSettings) -> str:
    return settings.api_base_url.rstrip("/")


async def _request(
    settings: BotSettings,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(
            method,
            f"{_base_url(settings)}{path}",
            params=params,
            json=json,
            headers=bot_headers(settings),
        )


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text


async def fetch_stats(settings: BotSettings, period_days: int) -> str:
    response = await _request(
        settings,
        "GET",
        "/api/internal/bot/admin/stats",
        params={"telegram_id": settings.admin_telegram_id, "period_days": period_days},
    )
    if response.status_code != 200:
        raise RuntimeError(f"stats API {response.status_code}: {_detail(response)}")
    return format_admin_stats(response.json())


async def fetch_pipeline_status(settings: BotSettings) -> str:
    response = await _request(
        settings,
        "GET",
        "/api/internal/bot/admin/sync-status",
        params={"telegram_id": settings.admin_telegram_id},
    )
    if response.status_code != 200:
        raise RuntimeError(f"sync-status API {response.status_code}: {_detail(response)}")
    return format_pipeline_status(response.json())


async def list_pipelines(settings: BotSettings) -> list[tuple[str, str]]:
    response = await _request(
        settings,
        "GET",
        "/api/internal/bot/admin/sync-pipelines",
        params={"telegram_id": settings.admin_telegram_id},
    )
    if response.status_code != 200:
        raise RuntimeError(f"sync-pipelines API {response.status_code}: {_detail(response)}")
    return [(item["key"], item["label"]) for item in response.json()]


async def enqueue_pipeline(
    settings: BotSettings, name: str, *, location_slug: str | None = None
) -> str:
    response = await _request(
        settings,
        "POST",
        "/api/internal/bot/admin/sync-enqueue",
        params={"telegram_id": settings.admin_telegram_id},
        json={"pipeline": name, "location_slug": location_slug},
    )
    if response.status_code == 400:
        raise ValueError(_detail(response) or "Неизвестный пайплайн")
    if response.status_code != 200:
        raise RuntimeError(f"sync-enqueue API {response.status_code}: {_detail(response)}")
    return response.json()["message"]


async def sync_protocol_url(settings: BotSettings, url: str) -> str:
    response = await _request(
        settings,
        "POST",
        "/api/internal/bot/admin/sync-protocol",
        params={"telegram_id": settings.admin_telegram_id},
        json={"url": url},
        # Перечитка протокола идёт через общую очередь фетчей — ждём дольше.
        timeout=120.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"sync-protocol API {response.status_code}: {_detail(response)}")
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
