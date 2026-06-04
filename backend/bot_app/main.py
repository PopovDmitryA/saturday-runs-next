from __future__ import annotations

import asyncio
import logging
import sys
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from bot_app.broadcast import (
    cmd_broadcast,
    cmd_broadcast_cancel,
    cmd_broadcast_send,
    cmd_broadcast_subscribers,
    handle_broadcast_draft_text,
    on_broadcast_callback,
    BROADCAST_CANCEL_CALLBACK,
    BROADCAST_SEND_CALLBACK,
)
from bot_app.settings import BotSettings, bot_headers

from app.legal.consent_text import consent_bot_message

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

LOGIN_CONSENT_PREFIX = "login_consent:"
LOGIN_DECLINE_PREFIX = "login_decline:"

settings = BotSettings()

LOGIN_MESSAGE = (
    "Ссылка для входа в личный кабинет.\n"
    "Она действует 5 минут и работает один раз."
)


def _bot_headers() -> dict[str, str]:
    return bot_headers(settings)


def _is_telegram_inline_button_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1", "0.0.0.0"}


async def _send_magic_link(message: Message, magic_link: str) -> None:
    if _is_telegram_inline_button_url(magic_link):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Войти в личный кабинет", url=magic_link)],
            ]
        )
        await message.answer(LOGIN_MESSAGE, reply_markup=keyboard)
        return

    logger.info("Magic link is not valid for Telegram button URL, sending as text: %s", magic_link)
    await message.answer(f"{LOGIN_MESSAGE}\n\n{magic_link}")


async def _needs_consent(telegram_id: int) -> bool:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/auth/bot/login-status",
            json={"telegram_id": telegram_id},
            headers=_bot_headers(),
        )
    if response.status_code != 200:
        logger.warning("login-status failed: %s %s", response.status_code, response.text)
        return True
    return bool(response.json().get("needs_consent", True))


async def confirm_login(
    message: Message,
    request_token: str,
    *,
    consent_accepted: bool,
    from_user=None,
) -> None:
    actor = from_user or message.from_user
    if actor is None or message.chat is None:
        return

    payload = {
        "request_token": request_token,
        "telegram_id": actor.id,
        "telegram_username": actor.username,
        "telegram_first_name": actor.first_name,
        "telegram_last_name": actor.last_name,
        "telegram_chat_id": message.chat.id,
        "consent_accepted": consent_accepted,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/auth/bot/confirm",
            json=payload,
            headers=_bot_headers(),
        )

    if response.status_code != 200:
        detail = response.json().get("detail", "Не удалось подтвердить вход.")
        await message.answer(f"Ошибка авторизации: {detail}")
        logger.warning("Bot confirm failed: %s %s", response.status_code, response.text)
        return

    magic_link = response.json().get("magic_link") or ""
    if not magic_link:
        await message.answer(
            "Telegram привязан. Вернитесь на сайт — там может потребоваться подтвердить объединение профилей."
        )
        return
    await _send_magic_link(message, magic_link)


def _consent_keyboard(request_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принимаю",
                    callback_data=f"{LOGIN_CONSENT_PREFIX}{request_token}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Не сейчас",
                    callback_data=f"{LOGIN_DECLINE_PREFIX}{request_token}",
                )
            ],
        ]
    )


async def _prompt_login_consent(message: Message, request_token: str) -> None:
    text = consent_bot_message(about_url=settings.app_base_url.rstrip("/"))
    await message.answer(text, reply_markup=_consent_keyboard(request_token))


async def _handle_coordinate_admin_message(message: Message) -> bool:
    if message.from_user is None or message.chat is None or not message.text:
        return False

    reply_to_message_id = None
    if message.reply_to_message is not None:
        reply_to_message_id = message.reply_to_message.message_id

    payload = {
        "telegram_chat_id": message.chat.id,
        "text": message.text.strip(),
        "reply_to_message_id": reply_to_message_id,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/internal/bot/coordinate-message",
            json=payload,
            headers=_bot_headers(),
        )
    if response.status_code != 200:
        return False
    data = response.json()
    if not data.get("handled"):
        return False
    reply = data.get("reply")
    if reply:
        await message.answer(reply, reply_to_message_id=message.message_id)
    return True


async def on_start(message: Message, command: CommandObject) -> None:
    if command.args and command.args.startswith("login_"):
        request_token = command.args.removeprefix("login_")
        if message.from_user is None:
            return
        if await _needs_consent(message.from_user.id):
            await _prompt_login_consent(message, request_token)
            return
        await confirm_login(message, request_token, consent_accepted=False)
        return

    await message.answer(
        "Привет! Я бот личного кабинета Saturday Runs.\n\n"
        "Saturday Runs собирает статистику из разных беговых систем в одном месте — "
        "чтобы проще решиться на первую пробежку там, где вы ещё не начинали.\n\n"
        "Чтобы войти на сайт, нажмите «Войти через Telegram» на странице входа.\n\n"
        "Координаты локаций с95: Reply на сообщение бота — latitude:longitude, "
        "затем Reply «ок» на сообщение с проверкой карты."
    )


async def on_login_callback(callback: CallbackQuery) -> None:
    if callback.data is None or callback.message is None:
        return

    if callback.data.startswith(LOGIN_DECLINE_PREFIX):
        await callback.answer("Вход отменён")
        await callback.message.answer(
            "Вход не выполнен. Без согласия на обработку данных личный кабинет недоступен.\n"
            f"Подробнее: {settings.app_base_url.rstrip('/')}/about#privacy"
        )
        return

    if not callback.data.startswith(LOGIN_CONSENT_PREFIX):
        return

    request_token = callback.data.removeprefix(LOGIN_CONSENT_PREFIX)
    await callback.answer("Принято, отправляю ссылку…")
    await confirm_login(
        callback.message,
        request_token,
        consent_accepted=True,
        from_user=callback.from_user,
    )


async def on_text(message: Message) -> None:
    if await handle_broadcast_draft_text(message, settings):
        return
    if await _handle_coordinate_admin_message(message):
        return


async def on_cmd_broadcast(message: Message) -> None:
    await cmd_broadcast(message, settings)


async def on_cmd_broadcast_send(message: Message) -> None:
    await cmd_broadcast_send(message, settings)


async def on_cmd_broadcast_cancel(message: Message) -> None:
    await cmd_broadcast_cancel(message, settings)


async def on_cmd_broadcast_subscribers(message: Message) -> None:
    await cmd_broadcast_subscribers(message, settings)


async def on_cmd_broadcast_callback(callback: CallbackQuery) -> None:
    await on_broadcast_callback(callback, settings)


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.message.register(on_start, CommandStart())
    dispatcher.message.register(on_cmd_broadcast, Command("broadcast"))
    dispatcher.message.register(on_cmd_broadcast_subscribers, Command("broadcast_subscribers"))
    dispatcher.message.register(on_cmd_broadcast_send, Command("broadcast_send"))
    dispatcher.message.register(on_cmd_broadcast_cancel, Command("broadcast_cancel"))
    dispatcher.message.register(on_text, F.text & ~F.text.startswith("/"))
    dispatcher.callback_query.register(
        on_login_callback,
        F.data.startswith(LOGIN_CONSENT_PREFIX) | F.data.startswith(LOGIN_DECLINE_PREFIX),
    )
    dispatcher.callback_query.register(
        on_cmd_broadcast_callback,
        F.data.in_({BROADCAST_SEND_CALLBACK, BROADCAST_CANCEL_CALLBACK}),
    )

    logger.info("Starting Telegram bot")
    await dispatcher.start_polling(bot)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
