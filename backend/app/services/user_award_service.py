"""Особые награды: выдача в админке и показ в витрине «Мои достижения».

Виды — app/award_kinds.py. Строка в user_awards, чей код из реестра пропал
(награду убрали из кода), не показывается нигде, но и не удаляется: вернули
вид — вернулась и награда.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.award_kinds import AWARD_BY_CODE, AWARD_KINDS
from app.models import User, UserAward


class AwardGrantError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _award_rows(db: Session, user_id: UUID) -> list[UserAward]:
    return (
        db.query(UserAward)
        .filter(UserAward.user_id == user_id)
        .order_by(UserAward.awarded_on, UserAward.created_at)
        .all()
    )


def list_user_awards(db: Session, user_id: UUID) -> list[dict[str, object]]:
    """Награды человека для витрины — по дате события, старые первыми."""
    items: list[dict[str, object]] = []
    for row in _award_rows(db, user_id):
        kind = AWARD_BY_CODE.get(row.code)
        if kind is None:
            continue
        items.append(
            {
                "code": kind.code,
                "title": kind.title,
                "icon": kind.icon,
                "emblem": kind.emblem,
                "caption": kind.caption,
                "tone": kind.tone,
                "description": kind.description,
                "awarded_on": row.awarded_on.isoformat(),
            }
        )
    return items


def _holder_label(user: User) -> str:
    name = (user.display_name or "").strip()
    return f"{name} (№{user.serial_id})" if name else f"№{user.serial_id}"


def admin_user_awards(db: Session, user_id: UUID) -> dict[str, object]:
    """Выданное человеку + каталог видов для формы выдачи.

    У награды в одном экземпляре каталог подсказывает, у кого она сейчас:
    вторую выдачу сервер всё равно отклонит, но админ видит это заранее.
    """
    awards = [
        {
            "id": str(row.id),
            "code": row.code,
            "title": AWARD_BY_CODE[row.code].title if row.code in AWARD_BY_CODE else row.code,
            "icon": AWARD_BY_CODE[row.code].icon if row.code in AWARD_BY_CODE else "❔",
            "awarded_on": row.awarded_on.isoformat(),
            "note": row.note,
            "created_at": row.created_at,
            "known": row.code in AWARD_BY_CODE,
        }
        for row in _award_rows(db, user_id)
    ]
    single_codes = [kind.code for kind in AWARD_KINDS if kind.single_holder]
    holders: dict[str, User] = {}
    if single_codes:
        for code, holder in (
            db.query(UserAward.code, User)
            .join(User, User.id == UserAward.user_id)
            .filter(UserAward.code.in_(single_codes))
            .all()
        ):
            holders[code] = holder
    kinds = []
    for kind in AWARD_KINDS:
        holder = holders.get(kind.code)
        kinds.append(
            {
                "code": kind.code,
                "title": kind.title,
                "icon": kind.icon,
                "description": kind.description,
                "single_holder": kind.single_holder,
                "holder": _holder_label(holder) if holder is not None and holder.id != user_id else None,
            }
        )
    return {"awards": awards, "kinds": kinds}


def grant_award(
    db: Session,
    user_id: UUID,
    *,
    code: str,
    awarded_on: date,
    note: str | None,
    granted_by_user_id: UUID,
) -> None:
    kind = AWARD_BY_CODE.get(code)
    if kind is None:
        raise AwardGrantError("Такой награды нет", status_code=404)
    existing = db.query(UserAward).filter(UserAward.user_id == user_id, UserAward.code == code).one_or_none()
    if existing is not None:
        raise AwardGrantError("Эта награда у человека уже есть", status_code=409)
    if kind.single_holder:
        holder = db.query(User).join(UserAward, UserAward.user_id == User.id).filter(UserAward.code == code).first()
        if holder is not None:
            raise AwardGrantError(
                f"Награда одна на весь сайт и уже у {_holder_label(holder)} — сначала отзовите её там",
                status_code=409,
            )
    db.add(
        UserAward(
            user_id=user_id,
            code=code,
            awarded_on=awarded_on,
            note=(note or "").strip() or None,
            granted_by_user_id=granted_by_user_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Двойной клик «Выдать»: вторая вставка упирается в уникальный ключ.
        db.rollback()
        raise AwardGrantError("Эта награда у человека уже есть", status_code=409) from None


def revoke_award(db: Session, user_id: UUID, award_id: UUID) -> bool:
    row = db.query(UserAward).filter(UserAward.id == award_id, UserAward.user_id == user_id).one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
