"""Единая точка admin-уведомлений от фич сайта.

Канал тот же, что у суточной сводки автообновления: Telegram через прокси, а ВК
— только фолбэк, когда прокси легла (см. `admin_telegram_notify.send_admin_report`).
До 03.08.2026 бэклог слал напрямую в ВК.

На прогоне тестов уведомления не уходят никуда: pytest на локальном стеке
подхватывает боевой .env, и тесты бэклога прилетали админу живыми сообщениями
(«Новая карточка бэклога: [фича] «Идея»», «Комментарий … Первый»).
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.core.runtime_env import is_test_run
from app.services.admin_telegram_notify import send_admin_report, send_admin_telegram_message

logger = logging.getLogger(__name__)


def notify_admin(text: str) -> bool:
    """Уведомление админу в один конец: карточки бэклога, алерты синков.

    True — сообщение доставлено (алерты синков по этому признаку решают, помечать
    ли заявку отправленной).
    """
    if is_test_run():
        logger.info("Admin notify skipped: test run (%s)", text[:80])
        return False
    return send_admin_report(text)


def admin_dialog_chat_id() -> int | None:
    """Чат, куда уходят такие сообщения (он же — тот, откуда придёт Reply)."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return None
    return settings.telegram_admin_chat_id


def notify_admin_dialog(text: str, *, reply_to_message_id: int | None = None) -> tuple[int, int] | None:
    """Сообщение, на которое админ отвечает Reply: возвращает (chat_id, message_id).

    Только Telegram, без фолбэка в ВК: ответы админа разбирает бот через
    /internal/bot/coordinate-message, а слушателя ВК у нас нет с перевода
    admin-бота на Telegram — сообщение в ВК осталось бы без обработки ответа.
    """
    if is_test_run():
        logger.info("Admin dialog notify skipped: test run (%s)", text[:80])
        return None

    chat_id = admin_dialog_chat_id()
    if chat_id is None:
        logger.warning("Admin Telegram dialog not configured, skip message: %s", text[:80])
        return None

    message_id = send_admin_telegram_message(text, reply_to_message_id=reply_to_message_id)
    if message_id:
        return chat_id, message_id
    return None
