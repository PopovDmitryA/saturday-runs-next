"""Пересчёт снимка табло обхода (страницы /hq и /world).

Каждый заход на /hq пересчитывал всё заново, и почти всё время уходило на
`count(*) FROM runs` по 124 млн строк. Данные меняются медленно, поэтому
считаем их по расписанию, а страница читает готовое из hq_snapshot.

База обхода живёт на домашнем сервере и доступна проду через туннель, поэтому
задача идёт в общую очередь: она короткая и не должна занимать очереди синков.
"""

from __future__ import annotations

import logging

from app.services import sweep_hq_snapshot_service as snapshot
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="sweep_hq.refresh_snapshot")
def refresh_sweep_hq_snapshot_task() -> dict[str, object]:
    dsn = snapshot.world_dsn()
    if not dsn:
        logger.info("снимок /hq пропущен: PM_WORLD_DSN не задан")
        return {"ok": False, "reason": "PM_WORLD_DSN не задан"}

    import psycopg

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        result = snapshot.refresh(conn)
    logger.info("снимок /hq обновлён: %s", result)
    return result
