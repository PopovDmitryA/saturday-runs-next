"""Дыры в протоколах русского parkrun и строки-заглушки на их месте.

Протоколы parkrun по России закрыты, и наши данные собраны со страниц профилей:
у каждой пробежки в профиле есть место на забеге. Кого профиля нет — безымянный
финишёр parkrun или удалённый аккаунт, — того нет и в протоколе, а места идут с
пропусками: 29 финишёров и номера до 41. Мировой обход атлетов (август 2026)
закрыл 0,26% таких дыр, остальные добрать неоткуда.

Поэтому пропуск внутри протокола занимает строка «неизвестного»: без участника,
без времени, status='unknown' — как у 5 вёрст. Число финишёров тогда совпадает с
последним номером, а безымянный не попадает ни в уникальных участников, ни во
времена и рекорды. Хвост протокола (последних финишёров без профиля) узнать
нельзя — заглушки ставятся только между известными местами.

Если потом профиль приносит настоящего бегуна на это место, заглушку он
вытесняет (см. release_placeholder).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import RunResult

PLACEHOLDER_KEY_PREFIX = "parkrun:unknown:"
PLACEHOLDER_STATUS = "unknown"


def placeholder_key(location_external_key: str, event_date: date, position: int) -> str:
    return f"{PLACEHOLDER_KEY_PREFIX}{location_external_key}:{event_date.isoformat()}:{position}"


def missing_positions(positions: list[int | None]) -> list[int]:
    """Места от 1 до последнего известного, которых нет в протоколе."""
    known = {position for position in positions if position is not None and position > 0}
    if not known:
        return []
    return [position for position in range(1, max(known) + 1) if position not in known]


def release_placeholder(db: Session, event_id: object, position: int | None) -> int:
    """Убрать заглушку с места, куда встаёт настоящий финишёр."""
    if position is None:
        return 0
    result = db.execute(
        delete(RunResult).where(
            RunResult.event_id == event_id,
            RunResult.position == position,
            RunResult.participant_id.is_(None),
            RunResult.external_result_key.startswith(PLACEHOLDER_KEY_PREFIX),
        )
    )
    return int(result.rowcount or 0)
