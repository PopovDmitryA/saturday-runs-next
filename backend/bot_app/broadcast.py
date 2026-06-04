from __future__ import annotations

import logging

import httpx
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot_app.settings import BotSettings, bot_headers

logger = logging.getLogger(__name__)

BROADCAST_SEND_CALLBACK = "broadcast_send"
BROADCAST_CANCEL_CALLBACK = "broadcast_cancel"


def _effective_admin_id(settings: BotSettings) -> int:
    if settings.admin_telegram_id > 0:
        return settings.admin_telegram_id
    return settings.telegram_admin_chat_id


def is_broadcast_admin(settings: BotSettings, telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    admin_id = _effective_admin_id(settings)
    return admin_id > 0 and telegram_id == admin_id


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data=BROADCAST_SEND_CALLBACK),
                InlineKeyboardButton(text="❌ Отмена", callback_data=BROADCAST_CANCEL_CALLBACK),
            ]
        ]
    )


def _format_subscriber_line(index: int, item: dict[str, object]) -> str:
    username = item.get("telegram_username")
    display_name = item.get("display_name")
    telegram_id = item.get("telegram_id")
    parts: list[str] = [f"{index}."]
    if username:
        parts.append(f"@{username}")
    elif display_name:
        parts.append(str(display_name))
    else:
        parts.append(str(telegram_id))
    parts.append(f"(id {telegram_id})")
    return " ".join(parts)


def _format_subscribers_list(data: dict[str, object]) -> str:
    subscribers = data.get("subscribers") or []
    count = int(data.get("count", 0))
    if not isinstance(subscribers, list) or count == 0:
        return "📋 Подписчиков пока нет."
    lines = [f"📋 Подписчики ({count}):"]
    for index, item in enumerate(subscribers, start=1):
        if isinstance(item, dict):
            lines.append(_format_subscriber_line(index, item))
    return "\n".join(lines)


async def _fetch_subscribers(settings: BotSettings, telegram_id: int) -> dict[str, object] | None:
    response = await _api_request(
        settings,
        "GET",
        "/api/internal/bot/broadcast/subscribers",
        params={"telegram_id": telegram_id},
    )
    if response.status_code != 200:
        return None
    return response.json()


async def _api_request(
    settings: BotSettings,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await client.request(
            method,
            f"{settings.api_base_url.rstrip('/')}{path}",
            params=params,
            json=json,
            headers=bot_headers(settings),
        )


async def cmd_broadcast(message: Message, settings: BotSettings) -> None:
    if message.from_user is None:
        return
    if not is_broadcast_admin(settings, message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return

    response = await _api_request(
        settings,
        "POST",
        "/api/internal/bot/broadcast/start",
        json={"telegram_id": message.from_user.id},
    )
    if response.status_code != 200:
        detail = response.json().get("detail", "Не удалось начать рассылку.")
        await message.answer(f"Ошибка: {detail}")
        return

    data = response.json()
    count = data.get("count", 0)
    subscribers_block = _format_subscribers_list(data)
    await message.answer(
        f"{subscribers_block}\n\n"
        "📝 Режим создания рассылки\n\n"
        "✅ Бот готов принять текст.\n"
        "Отправьте следующим сообщением — его получат пользователи из списка выше.\n\n"
        "После текста покажу превью и кнопки подтверждения.\n\n"
        "Список: /broadcast_subscribers\n"
        "Отмена: /broadcast_cancel",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Текст рассылки для подписчиков…",
        ),
    )


async def cmd_broadcast_subscribers(message: Message, settings: BotSettings) -> None:
    if message.from_user is None:
        return
    if not is_broadcast_admin(settings, message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return

    data = await _fetch_subscribers(settings, message.from_user.id)
    if data is None:
        await message.answer("Не удалось загрузить список подписчиков.")
        return
    await message.answer(_format_subscribers_list(data))


async def cmd_broadcast_cancel(message: Message, settings: BotSettings) -> None:
    if message.from_user is None:
        return
    if not is_broadcast_admin(settings, message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return

    response = await _api_request(
        settings,
        "POST",
        "/api/internal/bot/broadcast/cancel",
        json={"telegram_id": message.from_user.id},
    )
    if response.status_code != 200:
        detail = response.json().get("detail", "Не удалось отменить рассылку.")
        await message.answer(f"Ошибка: {detail}")
        return
    await message.answer("Рассылка отменена, черновик удалён. Режим ввода текста выключен.")


async def _format_send_report(data: dict[str, object]) -> str:
    recipients = int(data.get("recipients", 0))
    sent = int(data.get("sent", 0))
    failed = int(data.get("failed", 0))
    lines = [
        "Рассылка завершена.",
        f"Получателей: {recipients}",
        f"Доставлено: {sent}",
    ]
    if failed:
        lines.append(f"Ошибок: {failed}")
        failures = data.get("failures") or []
        if isinstance(failures, list):
            for item in failures[:5]:
                if isinstance(item, dict):
                    label = item.get("label") or item.get("telegram_id")
                    error = item.get("error", "unknown")
                    lines.append(f"• {label}: {error}")
            if len(failures) > 5:
                lines.append(f"… и ещё {len(failures) - 5}")
    return "\n".join(lines)


async def cmd_broadcast_send(message: Message, settings: BotSettings) -> None:
    if message.from_user is None:
        return
    if not is_broadcast_admin(settings, message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    await _execute_broadcast_send(message, settings, message.from_user.id)


async def _execute_broadcast_send(message: Message, settings: BotSettings, telegram_id: int) -> None:
    response = await _api_request(
        settings,
        "POST",
        "/api/internal/bot/broadcast/send",
        json={"telegram_id": telegram_id},
    )
    if response.status_code == 400:
        detail = response.json().get("detail", "Черновик не найден.")
        await message.answer(
            f"{detail}\n\nСначала /broadcast и текст сообщения, затем подтверждение."
        )
        return
    if response.status_code != 200:
        detail = response.json().get("detail", "Не удалось отправить рассылку.")
        await message.answer(f"Ошибка: {detail}")
        return
    await message.answer(await _format_send_report(response.json()))


async def handle_broadcast_draft_text(message: Message, settings: BotSettings) -> bool:
    if message.from_user is None or not message.text:
        return False
    if not is_broadcast_admin(settings, message.from_user.id):
        return False

    draft_state = await _api_request(
        settings,
        "GET",
        "/api/internal/bot/broadcast/draft",
        params={"telegram_id": message.from_user.id},
    )
    if draft_state.status_code != 200:
        return False
    state = draft_state.json()
    if not state.get("awaiting_text"):
        return False

    text = message.text.strip()
    if not text:
        await message.answer("Текст рассылки не может быть пустым.")
        return True

    save_response = await _api_request(
        settings,
        "POST",
        "/api/internal/bot/broadcast/draft",
        json={"telegram_id": message.from_user.id, "message": text},
    )
    if save_response.status_code != 200:
        detail = save_response.json().get("detail", "Не удалось сохранить черновик.")
        await message.answer(f"Ошибка: {detail}")
        return True

    subscribers_response = await _fetch_subscribers(settings, message.from_user.id)
    count = 0
    subscribers_block = ""
    if subscribers_response:
        count = int(subscribers_response.get("count", 0))
        subscribers_block = _format_subscribers_list(subscribers_response) + "\n\n"

    preview = (
        f"{subscribers_block}"
        f"📋 Черновик рассылки ({count} получателей):\n\n"
        f"---\n{text}\n---\n\n"
        "Подтвердите отправку кнопкой ниже или командой /broadcast_send.\n"
        "Отмена: /broadcast_cancel"
    )
    await message.answer(preview, reply_markup=_confirm_keyboard())
    return True


async def on_broadcast_callback(callback: CallbackQuery, settings: BotSettings) -> None:
    if callback.data is None or callback.message is None or callback.from_user is None:
        return
    if not is_broadcast_admin(settings, callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    if callback.data == BROADCAST_CANCEL_CALLBACK:
        await callback.answer("Отменено")
        await cmd_broadcast_cancel(callback.message, settings)
        return

    if callback.data != BROADCAST_SEND_CALLBACK:
        return

    await callback.answer("Отправляю…")
    await _execute_broadcast_send(callback.message, settings, callback.from_user.id)
