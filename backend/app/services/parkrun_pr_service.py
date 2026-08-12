from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.personal_record_service import (
    recalculate_participants_cross_platform_personal_records,
    recalculate_personal_records,
)

logger = logging.getLogger(__name__)

PLATFORM_CODE = "parkrun"

# Пакетный режим пересчёта личных рекордов.
#
# Пересчёт после КАЖДОГО атлета — самая дорогая часть импорта: он поднимает всю
# историю участника и его кросс-платформенные рекорды. На очереди в сотни
# профилей это доминирует над самим фетчем (страница качается ~1 с). Внутри
# `deferred_pr_recalc()` импорт только запоминает затронутых участников, а
# считает их один раз в конце — тем же кодом, что и раньше.
_deferred: ContextVar[set[UUID] | None] = ContextVar("parkrun_deferred_pr", default=None)


@contextmanager
def deferred_pr_recalc() -> Iterator[set[UUID]]:
    """Отложить пересчёт PR: внутри блока участники копятся, а не считаются."""
    pending: set[UUID] = set()
    token = _deferred.set(pending)
    try:
        yield pending
    finally:
        _deferred.reset(token)


def note_or_recalculate(db: Session, participant_id: UUID) -> bool:
    """Отметить участника к пересчёту (пакетный режим) или посчитать сразу.

    Возвращает True, если пересчёт отложен."""
    pending = _deferred.get()
    if pending is not None:
        pending.add(participant_id)
        return True
    recalculate_parkrun_personal_records(db, participant_id=participant_id)
    return False


def flush_deferred_personal_records(db: Session, participant_ids: set[UUID]) -> dict[str, int]:
    """Один проход пересчёта по всем накопленным участникам."""
    if not participant_ids:
        return {"participants": 0, "runs_updated": 0, "pr_runs": 0}
    logger.info("parkrun PR: пакетный пересчёт по %d участникам", len(participant_ids))
    runs_updated = 0
    pr_runs = 0
    for pid in participant_ids:
        result = recalculate_personal_records(db, PLATFORM_CODE, participant_id=pid)
        runs_updated += result["runs_updated"]
        pr_runs += result["pr_runs"]
    # Кросс-платформенные рекорды считаются на уровне User и умеют принимать
    # сразу набор участников — здесь это один запрос вместо N.
    recalculate_participants_cross_platform_personal_records(db, participant_ids)
    return {
        "participants": len(participant_ids),
        "runs_updated": runs_updated,
        "pr_runs": pr_runs,
    }


def recalculate_parkrun_personal_records(
    db: Session,
    *,
    participant_id: UUID | None = None,
) -> dict[str, int]:
    result = recalculate_personal_records(db, PLATFORM_CODE, participant_id=participant_id)
    if participant_id is not None:
        recalculate_participants_cross_platform_personal_records(db, {participant_id})
    return {
        "participants_touched": result["participants_touched"],
        "runs_updated": result["runs_updated"],
        "pr_runs": result["pr_runs"],
    }
