from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.admin_telegram_notify import send_admin_report
from app.services.scheduled_run_log_service import (
    DEFAULT_RETENTION_DAYS,
    cleanup_old_runs,
    recent_problem_runs,
    summarize,
)
from app.services.vk_admin_notify import format_daily_summary
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DIGEST_TIMEZONE = ZoneInfo("Europe/Moscow")
# Сколько проблемных запусков перечислять в сводке; полный список — в админке.
PROBLEMS_IN_SUMMARY = 5


def _day_bounds(day: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(day.date(), time.min, tzinfo=DIGEST_TIMEZONE)
    return start, start + timedelta(days=1)


@celery_app.task(name="admin_digest.daily_sync_summary")
def daily_sync_summary_task() -> dict[str, object]:
    """Одна сводка в сутки в ВК: сколько раз запускали и что обновилось.

    Заменяет прежние два сообщения на каждый запуск. Детали по локациям в
    сводку не попадают — за ними админка «Автообновление» (/admin/sync-runs).
    Здесь же чистим историю запусков старше retention.
    """
    settings = get_settings()
    now = datetime.now(DIGEST_TIMEZONE)
    start, end = _day_bounds(now)
    day_label = start.strftime("%d.%m.%Y")

    db = get_session_factory()()
    try:
        platforms = summarize(db, start=start, end=end)
        problems = recent_problem_runs(db, start=start, end=end, limit=PROBLEMS_IN_SUMMARY)
        deleted = cleanup_old_runs(db, retention_days=DEFAULT_RETENTION_DAYS)
    finally:
        db.close()

    admin_url = f"{settings.app_base_url.rstrip('/')}/admin/sync-runs"
    send_admin_report(format_daily_summary(day_label, platforms, problems=problems, admin_url=admin_url))

    if deleted:
        logger.info("Автообновление: удалено %s записей истории старше %s дней", deleted, DEFAULT_RETENTION_DAYS)

    return {
        "day": day_label,
        "platforms": len(platforms),
        "runs": sum(item["runs"] for item in platforms),
        "problems": sum(item["problems"] for item in platforms),
        "deleted_logs": deleted,
    }
