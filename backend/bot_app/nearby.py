"""Локации рядом: геопозиция в личке бота и inline-режим в любом чате.

Человек присылает геопозицию — бот отвечает списком ближайших локаций (правило
выбора, тексты и личное — на стороне api, nearby_locations_service). Под каждой
локацией две кнопки: точка на карте Telegram (оттуда маршрут в Яндекс или
Google) и страница локации на run5k.run.

Inline-режим: `@бот` в любом чате — варианты локаций рядом (или по названию),
выбранная уходит в чат карточкой без личного. Включается один раз в BotFather:
/setinline и /setinlinegeo — без второго Telegram не присылает геопозицию, и
работает только поиск по названию.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from aiogram.enums import ChatAction
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InlineQueryResultUnion,
    InputTextMessageContent,
    LinkPreviewOptions,
    Message,
)

from bot_app.settings import BotSettings, bot_headers

logger = logging.getLogger(__name__)

NEARBY_PIN_PREFIX = "nearpin:"
# Лимит Telegram на callback_data — 64 байта; ключ «catalog:<uuid>» занимает 44.
CALLBACK_DATA_MAX_BYTES = 64
# Кнопка «на карте» с названием: длинное имя Telegram обрежет многоточием сам,
# но тогда не видно, какая это кнопка, — режем заранее по слову.
PIN_BUTTON_NAME_MAX = 22
INLINE_HELP_START = "inline_help"
INLINE_CACHE_SECONDS = 60

API_UNAVAILABLE_MESSAGE = "Сайт сейчас не отвечает. Попробуйте ещё раз через минуту."

INLINE_HELP_MESSAGE = (
    "Как отправить локацию в любой чат\n\n"
    "Наберите в поле сообщения имя бота и пробел — появятся локации рядом с вами. "
    "Нажмите на нужную, и в чат уйдёт карточка: когда старт, какая погода, где сбор.\n\n"
    "Если списка нет, разрешите Telegram передавать геопозицию боту — он спросит об этом сам. "
    "Можно и без геопозиции: наберите после имени бота название парка или города."
)


def _is_public_https(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host not in {"localhost", "127.0.0.1", "0.0.0.0"}


def _short_name(name: str) -> str:
    if len(name) <= PIN_BUTTON_NAME_MAX:
        return name
    # +1 символ: если срез пришёлся ровно на конец слова, слово остаётся.
    cut = name[: PIN_BUTTON_NAME_MAX + 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def _pin_callback(identity_key: str) -> str | None:
    data = f"{NEARBY_PIN_PREFIX}{identity_key}"
    return data if len(data.encode("utf-8")) <= CALLBACK_DATA_MAX_BYTES else None


def nearby_keyboard(items: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    """Под каждой локацией: «📍 Имя на карте» и «Страница» на сайте."""
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        row: list[InlineKeyboardButton] = []
        callback = _pin_callback(str(item.get("identity_key") or ""))
        if callback:
            row.append(
                InlineKeyboardButton(text=f"📍 {_short_name(str(item.get('name') or ''))}", callback_data=callback)
            )
        site_url = item.get("site_url")
        if _is_public_https(site_url):
            row.append(InlineKeyboardButton(text="Страница на сайте", url=str(site_url)))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _site_button(url: str | None) -> InlineKeyboardMarkup | None:
    if not _is_public_https(url):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Страница на сайте", url=str(url))]])


def map_url(latitude: float, longitude: float) -> str:
    """Точка в Яндекс Картах — в inline-карточке вместо кнопки-коллбэка: её
    нажимают друзья в чужом чате, где у бота нет своего сообщения."""
    return f"https://yandex.ru/maps/?pt={longitude},{latitude}&z=16&l=map"


def inline_keyboard(item: dict[str, Any]) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="📍 На карте", url=map_url(float(item["latitude"]), float(item["longitude"])))]
    if _is_public_https(item.get("site_url")):
        row.append(InlineKeyboardButton(text="Страница на сайте", url=str(item["site_url"])))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def inline_description(item: dict[str, Any]) -> str:
    """Подпись варианта в списке inline: её видит только отправитель."""
    parts: list[str] = []
    if item.get("distance_km") is not None:
        distance = float(item["distance_km"])
        parts.append(
            f"{distance:.1f}".removesuffix(".0").replace(".", ",") + " км" if distance < 10 else f"{round(distance)} км"
        )
    if item.get("platform_title"):
        parts.append(str(item["platform_title"]))
    if item.get("start_label"):
        # «🗓 сб 4 октября, 9:00 · старт ≈№412» — значок в одну строку не нужен.
        parts.append(str(item["start_label"]).removeprefix("🗓 "))
    return " · ".join(parts)


def inline_results(items: list[dict[str, Any]]) -> list[InlineQueryResultUnion]:
    return [
        InlineQueryResultArticle(
            id=str(item["identity_key"])[:64],
            title=str(item["name"]),
            description=inline_description(item),
            input_message_content=InputTextMessageContent(
                message_text=str(item["text_html"]),
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ),
            reply_markup=inline_keyboard(item),
        )
        for item in items
    ]


async def _api_post(settings: BotSettings, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/internal/bot{path}",
            json=payload,
            headers=bot_headers(settings),
        )
    if response.status_code != 200:
        logger.warning("nearby %s failed: %s %s", path, response.status_code, response.text[:300])
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def _api_get(settings: BotSettings, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{settings.api_base_url.rstrip('/')}/api/internal/bot{path}",
            params=params,
            headers=bot_headers(settings),
        )
    if response.status_code != 200:
        logger.warning("nearby %s failed: %s %s", path, response.status_code, response.text[:300])
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def on_location(message: Message, settings: BotSettings) -> None:
    """Геопозиция в личке (в том числе трансляция и выбранное на карте место)."""
    if message.location is None or message.chat is None:
        return
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.FIND_LOCATION)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 — «ищет…» в шапке чата — украшение
        pass
    payload = {
        "latitude": message.location.latitude,
        "longitude": message.location.longitude,
        "telegram_id": message.from_user.id if message.from_user else None,
    }
    try:
        data = await _api_post(settings, "/nearby", payload)
    except httpx.HTTPError:
        logger.exception("nearby request failed")
        data = None
    if data is None:
        await message.answer(API_UNAVAILABLE_MESSAGE)
        return
    await message.answer(
        str(data.get("text_html") or ""),
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=nearby_keyboard(list(data.get("items") or [])),
    )


async def on_nearby_pin(callback: CallbackQuery, settings: BotSettings) -> None:
    """Кнопка «📍 Имя»: точка локации с адресом — Telegram сам предложит маршрут."""
    if callback.data is None or callback.message is None:
        return
    identity_key = callback.data.removeprefix(NEARBY_PIN_PREFIX)
    try:
        point = await _api_get(settings, "/nearby/point", {"identity_key": identity_key})
    except httpx.HTTPError:
        logger.exception("nearby point request failed")
        point = None
    if point is None:
        await callback.answer(API_UNAVAILABLE_MESSAGE, show_alert=True)
        return
    await callback.answer()
    # Через bot, а не callback.message.answer_venue: сообщение с кнопкой могло
    # стать недоступным (старое), а чат известен всегда.
    await callback.bot.send_venue(  # type: ignore[union-attr]
        chat_id=callback.message.chat.id,
        latitude=float(point["latitude"]),
        longitude=float(point["longitude"]),
        title=str(point.get("name") or "Локация"),
        address=str(point.get("address") or ""),
        reply_markup=_site_button(point.get("site_url")),
    )


async def on_inline_query(inline_query: InlineQuery, settings: BotSettings) -> None:
    location = inline_query.location
    query = (inline_query.query or "").strip()
    help_button = InlineQueryResultsButton(text="Как искать локации рядом", start_parameter=INLINE_HELP_START)
    if location is None and not query:
        # Геопозиции нет (не разрешили или /setinlinegeo не включён), текста
        # тоже — показывать нечего, кроме подсказки.
        await inline_query.answer([], cache_time=INLINE_CACHE_SECONDS, is_personal=True, button=help_button)
        return
    payload = {
        "latitude": location.latitude if location else None,
        "longitude": location.longitude if location else None,
        "query": query,
    }
    try:
        data = await _api_post(settings, "/nearby/inline", payload)
    except httpx.HTTPError:
        logger.exception("nearby inline request failed")
        data = None
    items = list((data or {}).get("items") or [])
    await inline_query.answer(
        inline_results(items),
        cache_time=INLINE_CACHE_SECONDS,
        # Ответ зависит от геопозиции — кэшировать его на всех нельзя.
        is_personal=True,
        button=None if items else help_button,
    )
