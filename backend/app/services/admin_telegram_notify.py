"""Admin-уведомления в Telegram через закреплённую прокси, с фолбэком в ВК.

Пока прокси жива, в ВК не приходит ничего — это единственный канал, который
Дмитрий реально смотрит. Если отправка в Telegram падает (прокси легла),
разово шлём алерт в ВК и дальше дублируем туда же каждый отчёт, пока прокси
не восстановится (тогда — разовое сообщение о восстановлении и снова тишина
в ВК). Состояние "прокси легла" хранится в Redis, чтобы не спамить алертом
на каждый последующий отчёт.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.core.rate_limit import get_redis
from app.services.vk_admin_notify import send_vk_admin_message

logger = logging.getLogger(__name__)

DEGRADED_FLAG_KEY = "admin:telegram_proxy_degraded"


def send_admin_telegram_message(text: str) -> bool:
    """POST в Telegram Bot API через telegram_proxy_url (если задан)."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        logger.info("Admin Telegram notify skipped: token or chat id not configured")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    proxy = settings.telegram_proxy_url or None
    try:
        response = httpx.post(
            url,
            json={
                "chat_id": settings.telegram_admin_chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            proxy=proxy,
            timeout=30.0,
        )
    except httpx.HTTPError:
        logger.exception("Admin Telegram notify failed (proxy=%s)", bool(proxy))
        return False

    if response.status_code != 200:
        logger.warning("Admin Telegram notify failed: %s %s", response.status_code, response.text)
        return False
    return True


def _is_degraded() -> bool:
    return bool(get_redis().get(DEGRADED_FLAG_KEY))


def _set_degraded(value: bool) -> None:
    redis = get_redis()
    if value:
        redis.set(DEGRADED_FLAG_KEY, "1")
    else:
        redis.delete(DEGRADED_FLAG_KEY)


def send_admin_report(text: str) -> None:
    """Точка входа для регулярных admin-отчётов (сейчас — суточная сводка синков)."""
    ok = send_admin_telegram_message(text)

    if ok:
        if _is_degraded():
            _set_degraded(False)
            send_vk_admin_message("✅ Прокси до Telegram восстановлена, отчёты снова только там.")
        return

    was_degraded = _is_degraded()
    if not was_degraded:
        _set_degraded(True)
        send_vk_admin_message("⚠️ Telegram-прокси легла, дублирую отчёты сюда до восстановления.")
    send_vk_admin_message(text)
