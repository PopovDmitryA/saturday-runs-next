"""Наблюдатель за приоритетными очередями — раз в 15 минут, задача внутри Redis.

Живёт в общей очереди намеренно: сторож не имеет права стоять в той же очереди,
за которой следит, иначе он молчит ровно тогда, когда нужен.
"""

from __future__ import annotations

import logging

from app.services.priority_queue_watch import watch_priority_queues
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="queues.watch_priority")
def watch_priority_queues_task() -> dict[str, object]:
    result = watch_priority_queues()
    if result.get("stalled"):
        logger.warning("Приоритетные очереди стоят: %s", result.get("queues"))
    return result
