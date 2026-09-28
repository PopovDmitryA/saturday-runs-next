"""Смены статусов карточек бэклога — одним сообщением на человека.

Статусы Дмитрий проставляет пачкой: разобрал бэклог, перевёл три карточки в
«реализовано» — и подписчик получал три сообщения подряд. Поэтому смена не
уходит сразу, а кладётся в Redis в группу по получателю. Beat-задача
`notifications.flush_backlog_statuses` раз в минуту отправляет группы, где
никто ничего не добавлял QUIET_SECONDS (разбор закончился) или которые
копятся дольше MAX_WAIT_SECONDS.

Одна карточка в группе — прежний текст с подробностью; несколько — список
строк «статус · название». Благодарность «спасибо, что предложили» остаётся
личной: она добавляется, только если среди реализованных есть карточка
самого человека.

Redis недоступен — отправляем сразу, по одной: лучше три сообщения, чем ни
одного.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import cast
from uuid import UUID

logger = logging.getLogger(__name__)

# Тишина в группе, после которой разбор считаем законченным.
QUIET_SECONDS = 120
# Потолок ожидания: долгий разбор не должен держать сообщение вечно.
MAX_WAIT_SECONDS = 15 * 60
GROUP_TTL_SECONDS = 24 * 3600

KEY_PREFIX = "notifications:backlog_status"
GROUPS_KEY = f"{KEY_PREFIX}:groups"


@dataclass(frozen=True)
class StatusChange:
    """Одна смена статуса для одного получателя."""

    card_id: str
    title: str
    url: str
    status: str
    label: str
    icon: str
    # Своя ли это карточка: от этого зависит благодарность в тексте.
    is_author: bool


def _key(user_id: str) -> str:
    return f"{KEY_PREFIX}:{user_id}"


def add_change(user_id: UUID, change: StatusChange, *, now: float | None = None) -> bool:
    """Положить смену в группу получателя. False — Redis недоступен, вызывающий
    отправляет сам."""
    now = time.time() if now is None else now
    try:
        from app.core.redis_client import get_redis_client

        pipe = get_redis_client().pipeline(transaction=True)
        pipe.rpush(_key(str(user_id)), json.dumps(asdict(change), ensure_ascii=False))
        pipe.expire(_key(str(user_id)), GROUP_TTL_SECONDS)
        # Отметка начала группы: по ней работает потолок ожидания.
        pipe.zadd(GROUPS_KEY, {str(user_id): now}, nx=True)
        pipe.hset(f"{KEY_PREFIX}:last", str(user_id), str(now))
        pipe.execute()
    except Exception:  # noqa: BLE001 — без Redis шлём сразу, по одной
        logger.exception("notify: backlog status buffer unavailable")
        return False
    return True


def take(user_id: str) -> list[StatusChange]:
    """Забрать группу целиком и атомарно."""
    from app.core.redis_client import get_redis_client

    pipe = get_redis_client().pipeline(transaction=True)
    pipe.lrange(_key(user_id), 0, -1)
    pipe.delete(_key(user_id))
    pipe.zrem(GROUPS_KEY, user_id)
    pipe.hdel(f"{KEY_PREFIX}:last", user_id)
    raw, _, _, _ = pipe.execute()
    changes: list[StatusChange] = []
    for item in raw or []:
        try:
            changes.append(StatusChange(**json.loads(item)))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("notify: битая запись в группе статусов %s", user_id)
    return changes


def ready_groups(*, now: float | None = None, force: bool = False) -> list[str]:
    """Получатели, чьи группы пора отправлять."""
    from app.core.redis_client import get_redis_client

    now = time.time() if now is None else now
    client = get_redis_client()
    groups = cast(list[tuple[str, float]], client.zrange(GROUPS_KEY, 0, -1, withscores=True))
    ready: list[str] = []
    for user_id, first_added in groups:
        if force:
            ready.append(user_id)
            continue
        last = client.hget(f"{KEY_PREFIX}:last", user_id)
        quiet_since = float(cast(str, last)) if last else first_added
        if quiet_since <= now - QUIET_SECONDS or first_added <= now - MAX_WAIT_SECONDS:
            ready.append(user_id)
    return ready
