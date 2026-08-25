from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.user_display_name_service import refresh_all_display_names
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="user_names.refresh")
def refresh_display_names_task() -> dict[str, object]:
    """Пересчёт имён пользователей из профилей беговых систем.

    Отдельных хуков в пяти путях синка (five_verst, s95, parkrun, runpark) нет
    намеренно: смена фамилии в профиле случается раз в годы, а обойти ~700
    пользователей одним запросом дешевле, чем поддерживать пять точек вызова.
    Немедленно имя пересчитывается только там, где человек смотрит на результат:
    привязка и отвязка профиля, объединение аккаунтов.
    """
    db = get_session_factory()()
    try:
        changed = refresh_all_display_names(db)
        if changed:
            logger.info("user_names: имя изменилось у %s пользователей", changed)
            # Таблицы рейтингов кэшируются: без прогрева новые имена появятся
            # в них только по TTL.
            from app.workers.tasks.leaderboards_warm import schedule_leaderboards_warm

            schedule_leaderboards_warm()
        return {"ok": True, "changed": changed}
    finally:
        db.close()
