"""Commit after each sync iteration so partial progress survives crashes."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import EventSummary, SyncStatus


def commit_step(db: Session) -> None:
    db.commit()


def rollback_step(db: Session) -> None:
    db.rollback()


def release_before_fetch(db: Session) -> None:
    """Закрыть транзакцию перед походом в сеть.

    На проде стоит `idle_in_transaction_session_timeout=60s`: Postgres рвёт
    соединение, которое висит «idle in transaction». Любое чтение из базы
    открывает транзакцию, и если следом идёт фетч на минуты, соединение
    умирает — а падает при этом первый запрос ПОСЛЕ фетча, из-за чего ошибка
    показывает совершенно невиновный SELECT.

    Так за 03.09.2026 упали четыре прохода «отмены ближайшего старта» у s95 и
    один клуб у 5 вёрст. Вызывать перед сетевой работой, когда писать ещё
    нечего: коммит здесь ничего не сохраняет, он только отпускает транзакцию.
    """
    db.commit()


def persist_step_error(db: Session, *, apply: Callable[[Session], None]) -> None:
    """Rollback failed iteration, persist error markers, commit."""
    db.rollback()
    try:
        apply(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


def mark_event_summary_error(
    db: Session,
    *,
    platform_id: UUID,
    external_event_key: str,
    message: str,
) -> None:
    row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform_id,
            EventSummary.external_event_key == external_event_key,
        )
        .one_or_none()
    )
    if row is None:
        return
    row.sync_status = SyncStatus.error
    row.error_message = message
    db.flush()


def persist_summary_error(
    db: Session,
    *,
    platform_id: UUID,
    external_event_key: str,
    message: str,
) -> None:
    persist_step_error(
        db,
        apply=lambda session: mark_event_summary_error(
            session,
            platform_id=platform_id,
            external_event_key=external_event_key,
            message=message,
        ),
    )
