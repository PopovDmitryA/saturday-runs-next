"""Перевесить отзывы с удаляемых строк результата на выжившие.

С миграции 091 удаление строки run_results / volunteer_results отзыв уже не
уносит (FK стал ON DELETE SET NULL), но ссылка обнуляется, и в «Моих оценках»
пропадают время и место старта. Если на том же старте у того же участника
осталась строка — перечитанный протокол выдал другой external_result_key,
дедуп оставил более полную копию — переносим отзыв на неё, чтобы связь осталась
живой. Отдельный модуль, а не кусок upsert.py: там и так две тысячи строк, а
эта логика про отзывы, а не про протоколы (аудит 09.2026, SCHEMA-01).
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import LocationRating

logger = logging.getLogger(__name__)


def relink_ratings_before_delete(
    db: Session,
    *,
    is_run: bool,
    moves: dict[UUID, UUID | None],
) -> int:
    """Перед db.delete() строк результата перевести их отзывы на замену.

    moves: id удаляемой строки -> id выжившей строки того же участника на том
    же старте (или None, если замены нет — тогда ссылка обнуляется явно, и
    отзыв остаётся на своём естественном ключе). Возвращает число тронутых
    отзывов; flush нужен сразу, иначе ORM удалит строку раньше, чем UPDATE
    отзыва уйдёт в базу, и SET NULL затрёт перенос.
    """
    if not moves:
        return 0
    column = LocationRating.run_result_id if is_run else LocationRating.volunteer_result_id
    ratings = db.query(LocationRating).filter(column.in_(list(moves))).all()
    for rating in ratings:
        setattr(rating, column.key, moves.get(getattr(rating, column.key)))
    if ratings:
        db.flush()
        logger.info("Отзывы перевешены с удаляемых строк результата: %d", len(ratings))
    return len(ratings)
