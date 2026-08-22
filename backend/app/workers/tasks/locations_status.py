"""Правило молчания: площадка без стартов дольше порога — «не действует».

Считается по датам протоколов, поэтому работает в обе стороны: вернувшаяся
площадка снимает статус сама. Реестр систем сильнее — то, что помечено руками
владельца, правило не трогает (см. location_activity_status).
"""

from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.sync.mark_inactive_locations_paused import mark_inactive_locations_paused
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# Очередь default: правило ходит по локациям и их сводкам, тяжёлых сетевых
# запросов не делает, занимать им очередь синков незачем.
@celery_app.task(name="locations.refresh_activity_status")
def refresh_location_activity_status() -> dict[str, object]:
    db = get_session_factory()()
    try:
        result = mark_inactive_locations_paused(db)
        db.commit()
        payload = {
            "checked": result.locations_checked,
            "paused": result.locations_paused,
            "revived": result.locations_revived,
        }
        logger.info("Статусы активности пересчитаны: %s", payload)
        return payload
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
