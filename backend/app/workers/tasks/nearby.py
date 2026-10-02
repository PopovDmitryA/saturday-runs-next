"""Подпись белых пятен журнала «где ищут старт» (очередь celery по умолчанию).

Обратный геокодинг — Nominatim с паузой в секунду между запросами: держать
на нём ответ бота нельзя, поэтому роут пишет строку и ставит задачу, а место
подписывает воркер. Не вышло — не беда: следующий запрос из той же клетки
поставит задачу снова, а отчёт и без подписи покажет координаты.
"""

from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.nearby_query_log_service import label_place
from app.workers.celery_app import celery_app
from app.workers.time_limits import LIMITS_SHORT

logger = logging.getLogger(__name__)


@celery_app.task(name="nearby.label_place", **LIMITS_SHORT)
def label_place_task(row_id: int) -> str | None:
    db = get_session_factory()()
    try:
        return label_place(db, row_id)
    except Exception:  # noqa: BLE001 — подпись необязательна, повтор придёт сам
        logger.warning("nearby: не удалось подписать клетку строки %s", row_id, exc_info=True)
        db.rollback()
        return None
    finally:
        db.close()


def queue_label_place(row_id: int) -> None:
    try:
        label_place_task.delay(row_id)
    except Exception:  # noqa: BLE001 — брокер лежит: подпись подождёт
        logger.warning("nearby: не удалось поставить подпись клетки в очередь", exc_info=True)
