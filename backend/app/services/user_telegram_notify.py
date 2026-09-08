"""Личное сообщение пользователю сайта от бота.

До заявок на волонтёрство бот писал людям только новостной рассылкой
(news_broadcast_service) — и та ходила мимо прокси. Здесь один канал для
адресных уведомлений (организатору о новой заявке, участнику о решении):
через telegram_proxy_url, как admin-уведомления, потому что прод не видит
api.telegram.org напрямую.

chat_id есть только у тех, кто хоть раз писал боту (виджет входа его не
отдаёт) — вызывающий код сам решает, что делать с молчащими получателями.
На прогоне тестов ничего не уходит.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.core.runtime_env import is_test_run

logger = logging.getLogger(__name__)


def send_user_telegram_message(chat_id: int | None, text: str) -> bool:
    if not chat_id:
        return False
    if is_test_run():
        logger.info("User Telegram notify skipped: test run (%s)", text[:60])
        return False
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("User Telegram notify skipped: bot token not configured")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        response = httpx.post(url, json=payload, proxy=settings.telegram_proxy_url or None, timeout=30.0)
    except httpx.HTTPError:
        logger.exception("User Telegram notify failed for chat %s", chat_id)
        return False
    if response.status_code != 200:
        logger.warning(
            "User Telegram notify failed for chat %s: %s %s",
            chat_id,
            response.status_code,
            response.text[:200],
        )
        return False
    return True
