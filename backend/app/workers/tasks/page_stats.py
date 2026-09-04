from __future__ import annotations

import logging

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.auth_service import purge_old_one_time_tokens
from app.services.login_journal_service import purge_old_login_events
from app.services.page_analytics_service import cleanup_old_events, rollup_recent_days
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="page_stats.rollup")
def rollup_task() -> dict[str, object]:
    """Пересборка дневных агрегатов page_stats_daily за сегодня и вчера (МСК)
    + удаление сырых событий старше retention. Идемпотентна, ходит каждый час:
    страница «Популярность» читает только агрегаты, лаг данных — до часа.

    Заодно чистит журнал входов и отработавшие одноразовые токены: отдельная
    beat-задача ради одного DELETE избыточна."""
    settings = get_settings()
    db = get_session_factory()()
    try:
        groups = rollup_recent_days(db, days=2)
        deleted = cleanup_old_events(db, retention_days=settings.page_events_retention_days)
        if deleted:
            logger.info("page_stats: удалено %s сырых событий старше %s дней", deleted, settings.page_events_retention_days)
        # Журнал входов чистим здесь же — отдельная beat-задача ради одного
        # DELETE в год избыточна.
        deleted_logins = purge_old_login_events(db, retention_days=settings.login_events_retention_days)
        if deleted_logins:
            logger.info(
                "page_stats: удалено %s событий журнала входов старше %s дней",
                deleted_logins,
                settings.login_events_retention_days,
            )
        # Одноразовые токены входа: строка остаётся после использования и
        # держит FK на users — из-за этого падало объединение аккаунтов.
        deleted_tokens = purge_old_one_time_tokens(
            db, retention_days=settings.auth_one_time_token_retention_days
        )
        if deleted_tokens:
            logger.info(
                "page_stats: удалено %s одноразовых токенов входа старше %s дней",
                deleted_tokens,
                settings.auth_one_time_token_retention_days,
            )
        return {
            "ok": True,
            "groups": groups,
            "deleted_events": deleted,
            "deleted_login_events": deleted_logins,
            "deleted_one_time_tokens": deleted_tokens,
        }
    finally:
        db.close()
