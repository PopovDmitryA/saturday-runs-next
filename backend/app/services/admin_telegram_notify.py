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


def send_admin_telegram_message(text: str, *, reply_to_message_id: int | None = None) -> int | None:
    """POST в Telegram Bot API через telegram_proxy_url (если задан).

    Возвращает `message_id` отправленного сообщения — он нужен диалогам, где админ
    отвечает Reply (заявки на координаты локаций). `0` — Telegram принял сообщение,
    но id не вернул; `None` — отправить не удалось.
    """
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        logger.info("Admin Telegram notify skipped: token or chat id not configured")
        return None

    payload: dict[str, object] = {
        "chat_id": settings.telegram_admin_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    proxy = settings.telegram_proxy_url or None
    try:
        response = httpx.post(url, json=payload, proxy=proxy, timeout=30.0)
    except httpx.HTTPError:
        logger.exception("Admin Telegram notify failed (proxy=%s)", bool(proxy))
        return None

    if response.status_code != 200:
        logger.warning("Admin Telegram notify failed: %s %s", response.status_code, response.text)
        return None
    return _message_id(response)


def _message_id(response: httpx.Response) -> int:
    try:
        result = response.json().get("result") or {}
        return int(result.get("message_id") or 0)
    except (ValueError, TypeError):
        return 0


def _is_degraded() -> bool:
    return bool(get_redis().get(DEGRADED_FLAG_KEY))


def _set_degraded(value: bool) -> None:
    redis = get_redis()
    if value:
        redis.set(DEGRADED_FLAG_KEY, "1")
    else:
        redis.delete(DEGRADED_FLAG_KEY)


def send_admin_report(text: str) -> bool:
    """Точка входа для admin-отчётов и алертов. True — сообщение доставлено.

    Результат важен алертам синков: они помечают заявку отправленной только после
    успеха, иначе алерт повторится на следующем запуске.
    """
    ok = send_admin_telegram_message(text) is not None

    if ok:
        if _is_degraded():
            _set_degraded(False)
            send_vk_admin_message("✅ Прокси до Telegram восстановлена, отчёты снова только там.")
        return True

    was_degraded = _is_degraded()
    if not was_degraded:
        _set_degraded(True)
        send_vk_admin_message("⚠️ Telegram-прокси легла, дублирую отчёты сюда до восстановления.")
    return send_vk_admin_message(text) is not None
