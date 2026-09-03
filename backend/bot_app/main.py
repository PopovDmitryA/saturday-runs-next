from __future__ import annotations

import asyncio
import logging
import sys
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.legal.consent_text import consent_bot_message
from bot_app import admin_ops
from bot_app.broadcast import (
    BROADCAST_CANCEL_CALLBACK,
    BROADCAST_SEND_CALLBACK,
    cmd_broadcast,
    cmd_broadcast_cancel,
    cmd_broadcast_send,
    cmd_broadcast_subscribers,
    handle_broadcast_draft_text,
    on_broadcast_callback,
)
from bot_app.settings import BotSettings, bot_headers

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# Кнопки под сообщением «откуда вход». Согласие и подтверждение — одна кнопка:
# новому человеку показываем условия и сразу подтверждаем вход, старому —
# только подтверждение. «Это не я» гасит запрос на сайте.
LOGIN_CONFIRM_PREFIX = "login_confirm:"
LOGIN_CONSENT_PREFIX = "login_consent:"
LOGIN_DECLINE_PREFIX = "login_decline:"

# Раз в полминуты бот отмечается в api после удачного запроса к Bot API: пока
# метка жива, сайт ведёт людей сюда, а не в виджет (core/bot_heartbeat.py).
HEARTBEAT_INTERVAL_SECONDS = 30

settings = BotSettings()

LOGIN_MESSAGE = (
    "Вход подтверждён ✅\n"
    "Вернитесь во вкладку сайта — она войдёт сама.\n\n"
    "Если вкладка закрылась, откройте кабинет по кнопке. "
    "Она действует 5 минут и работает один раз."
)

LOGIN_EXPIRED_MESSAGE = (
    "Эта ссылка для входа уже не действует.\n"
    "Откройте run5k.run и нажмите «Войти через Telegram» ещё раз."
)

LOGIN_DENIED_MESSAGE = (
    "Запрос отменён, вход не выполнен.\n\n"
    "Если вы не начинали вход на сайте, делать ничего не нужно: "
    "без подтверждения здесь войти в ваш профиль нельзя."
)

ADMIN_HELP_TEXT = (
    "Admin-команды:\n"
    "/stats [дней] — статистика ЛК\n"
    "/status — запущенные пайплайны и время старта\n"
    "/sync — список пайплайнов 5 вёрст и s95\n"
    "/sync registry — реестр /events/\n"
    "/sync latest — /results/latest/\n"
    "/sync rotation — ротация локаций (20 summary)\n"
    "/sync reconcile — сверка 100 старых протоколов\n"
    "/sync s95-latest — s95 /activities\n"
    "/sync s95-rotation — s95 ротация локаций\n"
    "/sync s95-reconcile — s95 сверка протоколов\n"
    "/sync s95-athletes — s95 реестр атлетов\n"
    "/sync s95-registry — s95 реестр /activities\n"
    "/sync location <slug> — одна локация\n"
    "/sync protocol <url> — протокол 5 вёрст или s95 по ссылке"
)


def _bot_headers() -> dict[str, str]:
    return bot_headers(settings)


def _is_telegram_inline_button_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1", "0.0.0.0"}


def format_login_context(context: dict[str, object], *, link_mode: bool = False) -> str:
    """Текст перед кнопкой «Подтвердить вход»: откуда пришёл запрос.

    Человек должен узнать свой телефон или ноутбук. Чужой запрос выглядит
    чужим: не тот браузер, не тот город — и его отклоняют кнопкой «Это не я».
    """
    title = "Привязка Telegram к профилю на run5k.run" if link_mode else "Вход на run5k.run"
    device = ", ".join(str(part) for part in (context.get("browser"), context.get("os")) if part)
    lines = [title, ""]
    lines.append(f"🖥 {device or 'устройство не определено'}")
    if context.get("city"):
        lines.append(f"📍 {context['city']}")
    if context.get("requested_at_label"):
        lines.append(f"🕒 {context['requested_at_label']}")
    lines.append("")
    lines.append(
        "Если это вы — подтвердите. Если вход начинали не вы, нажмите «Это не я»: "
        "запрос будет отменён."
    )
    return "\n".join(lines)


def _login_keyboard(request_token: str, *, with_consent: bool) -> InlineKeyboardMarkup:
    confirm_text = "✅ Принимаю условия и подтверждаю вход" if with_consent else "✅ Подтвердить вход"
    confirm_prefix = LOGIN_CONSENT_PREFIX if with_consent else LOGIN_CONFIRM_PREFIX
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=confirm_text, callback_data=f"{confirm_prefix}{request_token}")],
            [InlineKeyboardButton(text="🚫 Это не я", callback_data=f"{LOGIN_DECLINE_PREFIX}{request_token}")],
        ]
    )


async def _fetch_login_context(request_token: str, telegram_id: int) -> dict[str, object] | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/auth/bot/login-context",
            json={"request_token": request_token, "telegram_id": telegram_id},
            headers=_bot_headers(),
        )
    if response.status_code != 200:
        logger.warning("login-context failed: %s %s", response.status_code, response.text)
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def _deny_login(request_token: str, telegram_id: int) -> bool:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/auth/bot/deny",
            json={"request_token": request_token, "telegram_id": telegram_id},
            headers=_bot_headers(),
        )
    if response.status_code != 200:
        logger.warning("bot deny failed: %s %s", response.status_code, response.text)
        return False
    return True


async def _send_heartbeat() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{settings.api_base_url.rstrip('/')}/api/internal/bot/heartbeat",
            headers=_bot_headers(),
        )
    if response.status_code != 200:
        logger.warning("heartbeat failed: %s %s", response.status_code, response.text)


async def _heartbeat_loop(bot: Bot) -> None:
    """Пока Bot API отвечает (через прокси или VPN — неважно), отмечаемся в api.

    Проверка именно запросом к Telegram, а не «процесс жив»: бот в крэш-лупе
    из-за лежащей прокси метку не обновит, и сайт уведёт людей в виджет.
    """
    while True:
        try:
            await bot.get_me()
            await _send_heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — цикл не должен умирать от одной ошибки
            logger.warning("heartbeat skipped: %s", exc)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


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
            "Telegram привязан ✅ Вернитесь во вкладку сайта — она обновится сама. "
            "Если этим Telegram уже владеет другой профиль, сайт предложит объединить их."
        )
        return
    await _send_magic_link(message, magic_link)


async def _prompt_login_confirmation(message: Message, request_token: str, context: dict[str, object]) -> None:
    """Показать, откуда вход, и кнопки. Новому человеку — ещё и условия."""
    link_mode = bool(context.get("link_mode"))
    with_consent = bool(context.get("needs_consent")) and not context.get("consent_given")
    text = format_login_context(context, link_mode=link_mode)
    if with_consent:
        text = f"{consent_bot_message(about_url=settings.app_base_url.rstrip('/'))}\n\n{text}"
    await message.answer(text, reply_markup=_login_keyboard(request_token, with_consent=with_consent))


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


def _is_admin(telegram_id: int) -> bool:
    return settings.admin_telegram_id > 0 and telegram_id == settings.admin_telegram_id


async def _admin_only(message: Message) -> bool:
    if message.from_user is None or not _is_admin(message.from_user.id):
        await message.answer("Доступ только для администратора.")
        return False
    return True


async def _send_admin_reply(chat_id: int, text: str) -> None:
    """Отправка ответа admin-команды напрямую в Bot API (через ту же прокси, что и сам бот).

    Не `message.answer`, чтобы длинный отчёт уходил одним понятным вызовом
    с disable_web_page_preview и без разметки."""
    proxy = settings.telegram_proxy_url or None
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=30.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
    except httpx.HTTPError:
        logger.exception("Admin reply send failed (proxy=%s)", bool(proxy))


async def on_cmd_stats(message: Message, command: CommandObject) -> None:
    if not await _admin_only(message) or message.chat is None:
        return
    period_days = 30
    args = (command.args or "").split()
    if args:
        try:
            period_days = max(1, min(365, int(args[0])))
        except ValueError:
            await _send_admin_reply(message.chat.id, "Использование: /stats [дней]")
            return
    try:
        text = admin_ops.fetch_stats(settings, period_days)
    except Exception as exc:
        text = f"Не удалось получить статистику: {exc}"
    await _send_admin_reply(message.chat.id, text)


async def on_cmd_status(message: Message) -> None:
    if not await _admin_only(message) or message.chat is None:
        return
    try:
        text = admin_ops.fetch_pipeline_status(settings)
    except Exception as exc:
        text = f"Не удалось получить статус: {exc}"
    await _send_admin_reply(message.chat.id, text)


async def on_cmd_sync(message: Message, command: CommandObject) -> None:
    if not await _admin_only(message) or message.chat is None:
        return
    args = (command.args or "").split()

    if not args:
        try:
            pipelines = admin_ops.list_pipelines(settings)
        except Exception as exc:
            await _send_admin_reply(message.chat.id, f"Не удалось получить список пайплайнов: {exc}")
            return
        lines = ["Пайплайны 5 вёрст и s95:"]
        for key, label in pipelines:
            lines.append(f"• /sync {key} — {label}")
        lines.append("• /sync location <slug> — одна локация")
        await _send_admin_reply(message.chat.id, "\n".join(lines))
        return

    if args[0].lower() == "protocol":
        if len(args) < 2:
            await _send_admin_reply(
                message.chat.id,
                "Использование: /sync protocol <url>\n"
                "Примеры:\n"
                "• https://5verst.ru/zil/results/31.05.2025/\n"
                "• https://s95.ru/activities/4124",
            )
            return
        try:
            text = admin_ops.sync_protocol_url(settings, args[1])
        except Exception as exc:
            text = f"Не удалось обновить протокол: {exc}"
        await _send_admin_reply(message.chat.id, text)
        return

    try:
        location_slug = args[1] if args[0].lower() == "location" and len(args) > 1 else None
        reply = admin_ops.enqueue_pipeline(settings, args[0], location_slug=location_slug)
    except ValueError as exc:
        reply = str(exc)
    except Exception as exc:
        reply = f"Не удалось поставить в очередь: {exc}"
    await _send_admin_reply(message.chat.id, reply)


async def on_cmd_admin_help(message: Message) -> None:
    if not await _admin_only(message) or message.chat is None:
        return
    await _send_admin_reply(message.chat.id, ADMIN_HELP_TEXT)


async def on_start(message: Message, command: CommandObject) -> None:
    if command.args and command.args.startswith("login_"):
        request_token = command.args.removeprefix("login_")
        if message.from_user is None:
            return
        context = await _fetch_login_context(request_token, message.from_user.id)
        if context is None:
            await message.answer("Сайт сейчас не отвечает. Попробуйте ещё раз через минуту.")
            return
        if context.get("status") != "pending":
            await message.answer(LOGIN_EXPIRED_MESSAGE)
            return
        await _prompt_login_confirmation(message, request_token, context)
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

    # Кнопки одноразовые: убираем их, чтобы повторное нажатие не плодило запросов.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — сообщение могли удалить, это не повод молчать
        pass

    if callback.data.startswith(LOGIN_DECLINE_PREFIX):
        request_token = callback.data.removeprefix(LOGIN_DECLINE_PREFIX)
        await callback.answer("Запрос отменён")
        await _deny_login(request_token, callback.from_user.id)
        await callback.message.answer(LOGIN_DENIED_MESSAGE)
        return

    if callback.data.startswith(LOGIN_CONSENT_PREFIX):
        request_token = callback.data.removeprefix(LOGIN_CONSENT_PREFIX)
        consent_accepted = True
    elif callback.data.startswith(LOGIN_CONFIRM_PREFIX):
        request_token = callback.data.removeprefix(LOGIN_CONFIRM_PREFIX)
        consent_accepted = False
    else:
        return

    await callback.answer("Подтверждаю…")
    await confirm_login(
        callback.message,
        request_token,
        consent_accepted=consent_accepted,
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

    # Весь трафик бота (long-poll в том числе) идёт через прокси: с прод-сервера
    # api.telegram.org недоступен напрямую, без прокси polling падает на get_me().
    session = AiohttpSession(proxy=settings.telegram_proxy_url) if settings.telegram_proxy_url else None
    if session is not None:
        logger.info("Telegram bot uses proxy %s", settings.telegram_proxy_url)
    bot = Bot(token=settings.telegram_bot_token, session=session) if session else Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.message.register(on_start, CommandStart())
    dispatcher.message.register(on_cmd_broadcast, Command("broadcast"))
    dispatcher.message.register(on_cmd_broadcast_subscribers, Command("broadcast_subscribers"))
    dispatcher.message.register(on_cmd_broadcast_send, Command("broadcast_send"))
    dispatcher.message.register(on_cmd_broadcast_cancel, Command("broadcast_cancel"))
    dispatcher.message.register(on_cmd_stats, Command("stats"))
    dispatcher.message.register(on_cmd_status, Command("status"))
    dispatcher.message.register(on_cmd_sync, Command("sync"))
    dispatcher.message.register(on_cmd_admin_help, Command("admin_help"))
    dispatcher.message.register(on_text, F.text & ~F.text.startswith("/"))
    dispatcher.callback_query.register(
        on_login_callback,
        F.data.startswith(LOGIN_CONSENT_PREFIX)
        | F.data.startswith(LOGIN_CONFIRM_PREFIX)
        | F.data.startswith(LOGIN_DECLINE_PREFIX),
    )
    dispatcher.callback_query.register(
        on_cmd_broadcast_callback,
        F.data.in_({BROADCAST_SEND_CALLBACK, BROADCAST_CANCEL_CALLBACK}),
    )

    logger.info("Starting Telegram bot")
    heartbeat = asyncio.create_task(_heartbeat_loop(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        heartbeat.cancel()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
