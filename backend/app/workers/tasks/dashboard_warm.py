"""Прогрев дашбордов после батч-синка.

Раньше любой упсерт результатов 5 вёрст сносил dashboard_cache всем 500+
пользователям, а пересчёт (десятки секунд) доставался первому, кто откроет
профиль — вплоть до таймаута фронта. Теперь синк говорит «вот с какого момента
я трогал данные», а эта задача сама решает, кого это касается, сносит кэш
только им и тут же пересчитывает в воркере.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.db.session import get_session_factory
from app.services.dashboard_service import (
    invalidate_dashboard_cache_for_users,
    locations_touched_since,
    order_users_by_recent_login,
    recompute_dashboard_cache,
    users_holding_location_records,
    users_with_touched_results,
)
from app.services.location_records_service import warm_location_progressions
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Потолок на один прогон: синк, перезабравший разом много протоколов, не должен
# занимать воркер часами. Кэш всё равно снесён всем затронутым — непрогретые
# просто пересчитаются при заходе (секунды, а не десятки секунд, потому что
# прогрессии площадок к тому моменту уже в общем кэше).
MAX_USERS_PER_RUN = 200


@celery_app.task(name="dashboard_warm.after_sync")
def warm_dashboards_after_sync(since_iso: str) -> dict[str, object]:
    """Пересчитать дашборды тех, кого затронул синк.

    Очередь по умолчанию (сервис worker): задача упирается в БД и CPU, а не в
    сеть, и не должна вставать в хвост за фетчами в five_verst — там
    concurrency=1 и приоритет у пользовательских синков.
    """
    since = datetime.fromisoformat(since_iso)
    db = get_session_factory()()
    warmed = 0
    failed = 0
    skipped = 0
    try:
        locations = locations_touched_since(db, since)
        # Свои тронутые результаты плюс держатели рекордов: последним рекорд
        # мог перебить кто-то другой, и их дашборд устареет без их участия.
        user_ids = users_with_touched_results(db, since)
        if locations:
            user_ids |= users_holding_location_records(db)

        # Прогрессии — общие для всех, кто бегал на площадке, поэтому греем их
        # один раз до пересчёта дашбордов, а не внутри каждого.
        scopes = warm_location_progressions(db, locations)
        db.rollback()

        invalidate_dashboard_cache_for_users(db, user_ids)
        db.commit()

        ordered = order_users_by_recent_login(db, user_ids)
        skipped = max(0, len(ordered) - MAX_USERS_PER_RUN)
        if skipped:
            logger.warning(
                "dashboard warm capped at %d users, %d left for lazy recompute",
                MAX_USERS_PER_RUN,
                skipped,
            )
        for user_id in ordered[:MAX_USERS_PER_RUN]:
            try:
                recompute_dashboard_cache(db, user_id)
                db.commit()
                warmed += 1
            except Exception:
                logger.exception("dashboard warm failed for user %s", user_id)
                db.rollback()
                failed += 1
    finally:
        db.close()

    result: dict[str, object] = {
        "locations": len(locations),
        "scopes_computed": scopes,
        "users": len(user_ids),
        "warmed": warmed,
        "failed": failed,
        "skipped": skipped,
    }
    logger.info("dashboards warmed after sync: %s", result)
    return result
