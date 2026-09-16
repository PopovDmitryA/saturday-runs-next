"""Гашение зависших sync_runs по расписанию.

`close_stale_sync_runs` существует с 2026 года, но вызывался только из
`get_admin_pipeline_status`, то есть при открытии админской страницы
«Автообновление». Пока туда никто не заходил, записи висели в статусе running
неделями (12.09.2026 на проде нашлись висяки от 11.09). Рестарт воркера —
штатное событие: он случается на каждом деплое, а с `acks_late=True` у фоновых
задач 5 вёрст незавершённая задача просто возвращается в очередь, но исходная
строка sync_runs остаётся открытой навсегда.

Задача чисто внутри базы, сетевых запросов нет — поэтому общая очередь.
"""

from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.sync_run_maintenance import close_stale_sync_runs
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="sync_runs.close_stale")
def close_stale_sync_runs_task() -> dict[str, object]:
    db = get_session_factory()()
    try:
        closed = close_stale_sync_runs(db)
        if closed:
            logger.info("Погашено зависших sync_runs: %s", closed)
        return {"closed": closed}
    finally:
        db.close()
