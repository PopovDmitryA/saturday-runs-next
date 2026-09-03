"""Метка «бот жив»: по ней сайт решает, вести человека в бота или в виджет.

Сам api в Telegram не ходит ни при каком способе входа, поэтому «доступен ли
Telegram» с его стороны не проверить. Ходит бот — через tg-proxy или VPN, — и
он же раз в полминуты отмечается здесь после удачного запроса к Bot API.
Пропала метка — бот лежит, прокси легла или Telegram не отвечает; в любом из
этих случаев вход через бота не завершится, и сайт отправляет человека по
запасному пути — в Telegram Login Widget, которому сервер не нужен.
"""

from __future__ import annotations

from app.core.redis_client import get_redis_client

BOT_HEARTBEAT_KEY = "bot:heartbeat"


def mark_bot_alive(ttl_seconds: int) -> None:
    get_redis_client().setex(BOT_HEARTBEAT_KEY, ttl_seconds, "1")


def is_bot_alive() -> bool:
    try:
        return bool(get_redis_client().exists(BOT_HEARTBEAT_KEY))
    except Exception:  # noqa: BLE001 — без Redis считаем, что бота нет: виджет надёжнее
        return False
