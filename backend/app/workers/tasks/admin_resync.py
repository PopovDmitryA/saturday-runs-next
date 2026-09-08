"""Заявки «обновить по ссылке» из админки.

Две задачи — по одной на платформу — потому что у каждой своя приоритетная
очередь: `five_verst_user` (свой воркер, батчи 5 вёрст замирают между
фетчами) и `s95_user` (батч S95 уступает исключением). Контекст
пользовательского синка включается на всё время заявки — это и есть
«вне очереди, но внутри общей очереди».
"""

from __future__ import annotations

from uuid import UUID

from app.config import get_settings
from app.five_verst.fetch.priority import five_verst_user_sync_context
from app.s95.fetch.priority import s95_user_sync_context
from app.services.admin_resync_service import run_admin_resync
from app.workers.celery_app import celery_app


@celery_app.task(name="user_sync.admin_resync", queue="five_verst_user")
def admin_resync_five_verst_task(request_id: str) -> dict[str, object]:
    with five_verst_user_sync_context(ttl_seconds=get_settings().five_verst_user_sync_active_ttl_seconds):
        return run_admin_resync(UUID(request_id))


@celery_app.task(name="s95_sync.run_admin_resync", queue="s95_user")
def admin_resync_s95_task(request_id: str) -> dict[str, object]:
    with s95_user_sync_context():
        return run_admin_resync(UUID(request_id))
