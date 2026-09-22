"""Сторож приоритетных очередей: задача, которая ждёт дольше человеческого терпения.

17–19.09.2026 очередь `five_verst_fresh` не разбиралась двое суток, и узнали мы
об этом только когда Дмитрий увидел на сайте субботние старты с одним участником
на локацию. Сторож закрывает именно эту дыру: он смотрит не на воркеры (живой
воркер может быть занят чужой работой) и не на расписание (задача может быть
поставлена и забыта), а на то единственное, что важно, — сколько уже ждёт
старейшее сообщение.

Времени постановки в сообщении celery нет, поэтому возраст считаем сами: если
на хвосте очереди то же самое сообщение, что и в прошлый заход, значит с того
момента его так никто и не взял. Снимок живёт в Redis рядом с очередью.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.redis_client import get_redis_client
from app.services.admin_notify import notify_admin

logger = logging.getLogger(__name__)

# Очереди, где кто-то ждёт: сегодняшние протоколы и запросы людей из кабинета.
# Фоновые очереди сюда не входят намеренно — там ожидание в часах это норма.
PRIORITY_QUEUES: dict[str, str] = {
    "five_verst_fresh": "свежие протоколы 5 вёрст",
    "five_verst_user": "синки профилей 5 вёрст по кнопке",
    "s95_user": "синки профилей S95 по кнопке",
}

# Полчаса — потолок терпения: одна фоновая пачка идёт ~9 минут, и даже занятый
# воркер обязан добраться до приоритетной очереди за это время.
STALL_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class StalledQueue:
    queue: str
    title: str
    task: str
    waiting: timedelta
    depth: int


def _state_key(queue: str) -> str:
    return f"queue-stall:{queue}"


def _oldest_message(queue: str) -> tuple[str, str] | None:
    """(id, имя задачи) сообщения на хвосте — его воркер заберёт следующим."""

    raw = get_redis_client().lindex(queue, -1)
    if not raw:
        return None
    try:
        headers = json.loads(raw)["headers"]
        return str(headers["id"]), str(headers["task"])
    except (ValueError, KeyError, TypeError):
        logger.warning("Не разобрал сообщение из очереди %s", queue)
        return None


def check_priority_queues(now: datetime | None = None) -> list[StalledQueue]:
    """Очереди, которые стоят дольше STALL_AFTER. О каждой говорим один раз."""

    redis = get_redis_client()
    moment = now or datetime.now(timezone.utc)
    stalled: list[StalledQueue] = []

    for queue, title in PRIORITY_QUEUES.items():
        oldest = _oldest_message(queue)
        key = _state_key(queue)
        if oldest is None:
            redis.delete(key)
            continue

        message_id, task = oldest
        state = redis.hgetall(key) or {}
        if state.get("id") != message_id:
            redis.hset(key, mapping={"id": message_id, "first_seen": moment.isoformat()})
            redis.expire(key, int(STALL_AFTER.total_seconds()) * 8)
            continue

        first_seen = datetime.fromisoformat(state["first_seen"])
        waiting = moment - first_seen
        if waiting < STALL_AFTER or state.get("alerted") == "1":
            continue

        redis.hset(key, "alerted", "1")
        stalled.append(
            StalledQueue(
                queue=queue,
                title=title,
                task=task,
                waiting=waiting,
                depth=int(redis.llen(queue) or 0),
            )
        )
    return stalled


def format_stall_alert(stalled: list[StalledQueue]) -> str:
    lines = ["🐌 Очередь стоит"]
    for item in stalled:
        minutes = int(item.waiting.total_seconds() // 60)
        lines.append("")
        lines.append(f"• {item.queue} — {item.title}")
        lines.append(f"  Ждёт: {minutes} мин, задач в очереди: {item.depth}")
        lines.append(f"  Первая: {item.task}")
    lines.append("")
    lines.append("Проверьте, жив ли воркер этой очереди и не занят ли он фоном.")
    return "\n".join(lines)


def watch_priority_queues(now: datetime | None = None) -> dict[str, object]:
    stalled = check_priority_queues(now)
    if not stalled:
        return {"stalled": 0}
    notified = notify_admin(format_stall_alert(stalled))
    return {"stalled": len(stalled), "queues": [item.queue for item in stalled], "notified": notified}
