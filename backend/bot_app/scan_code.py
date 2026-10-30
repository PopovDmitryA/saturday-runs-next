"""Кнопка «Мой штрихкод» под напоминанием о плане: бот присылает QR-код
участника картинкой — на старте его не ищут по сайту.

Код — того, кто нажал (по его Telegram), а не адресата сообщения:
пересланное напоминание чужой штрихкод не откроет. Картинку рисует сайт
(app/services/scan_code_service.py) — тем же QR, что в «Профилях беговых
систем».
"""

from __future__ import annotations

import base64
import logging

import httpx
from aiogram.types import BufferedInputFile, CallbackQuery

from bot_app.settings import BotSettings, bot_headers

logger = logging.getLogger(__name__)

# Как SCAN_CODE_CALLBACK_PREFIX в app/services/plan_reminder_extras.py.
SCAN_CODE_PREFIX = "scancode:"

API_UNAVAILABLE_MESSAGE = "Сайт сейчас не отвечает. Попробуйте ещё раз через минуту."
NO_USER_MESSAGE = "Этот Telegram не привязан к профилю на run5k.run — войдите на сайт через Telegram."
NO_CODE_MESSAGE = "Не нашли штрихкод: привяжите профиль системы на run5k.run — «Обзор» → «Профили беговых систем»."


async def on_scan_code(callback: CallbackQuery, settings: BotSettings) -> None:
    if callback.data is None or callback.message is None or callback.from_user is None:
        return
    platform = callback.data.removeprefix(SCAN_CODE_PREFIX)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.api_base_url.rstrip('/')}/api/internal/bot/scan-code",
                params={"telegram_id": callback.from_user.id, "platform": platform},
                headers=bot_headers(settings),
            )
    except httpx.HTTPError:
        logger.exception("scan code request failed")
        await callback.answer(API_UNAVAILABLE_MESSAGE, show_alert=True)
        return
    if response.status_code == 404:
        detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else None
        await callback.answer(NO_USER_MESSAGE if detail == "no_user" else NO_CODE_MESSAGE, show_alert=True)
        return
    if response.status_code != 200:
        logger.warning("scan code failed: %s %s", response.status_code, response.text[:300])
        await callback.answer(API_UNAVAILABLE_MESSAGE, show_alert=True)
        return
    data = response.json()
    await callback.answer()
    name = data.get("display_name")
    caption = "\n".join(
        part
        for part in (
            f"<b>{data['code']}</b> · {data['platform_title']}",
            name,
            "Покажите этот код сканеру на финише.",
        )
        if part
    )
    # Через bot, а не callback.message.answer_photo: сообщение с кнопкой могло
    # стать недоступным (старое), а чат известен всегда.
    await callback.bot.send_photo(  # type: ignore[union-attr]
        chat_id=callback.message.chat.id,
        photo=BufferedInputFile(base64.b64decode(data["png_base64"]), filename=f"{data['code']}.png"),
        caption=caption,
        parse_mode="HTML",
    )
