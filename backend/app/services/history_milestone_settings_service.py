from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HistoryMilestoneSetting


def get_disabled_milestone_kinds(db: Session) -> set[str]:
    """Kind'ы, выключенные глобально (для всех пользователей сразу).

    Страницу «Вехи истории» из админки убрали 17.07.2026: каждый бегун управляет
    своими вехами сам (`/settings`, users.history_disabled_kinds). Глобальный
    выключатель остался как аварийный — на случай, если веху нужно срочно убрать
    у всех разом; строка заводится руками в history_milestone_settings. На проде
    таких строк нет, так что фильтр обычно пустой.
    """
    rows = db.query(HistoryMilestoneSetting.kind).filter(HistoryMilestoneSetting.enabled.is_(False)).all()
    return {row[0] for row in rows}
