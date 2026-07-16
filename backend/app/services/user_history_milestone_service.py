from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.history_milestone_kinds import MILESTONE_KIND_REGISTRY, MILESTONE_KINDS
from app.models import User


class UnknownMilestoneKindError(Exception):
    pass


def normalize_disabled_kinds(raw: object) -> list[str]:
    """Оставляет только валидные kind'ы в каноническом порядке реестра.

    Защита от мусора в JSONB (устаревшие/переименованные kind'ы, дубли, не-строки).
    """
    if not isinstance(raw, (list, tuple, set)):
        return []
    present = {item for item in raw if isinstance(item, str) and item in MILESTONE_KINDS}
    return [info.kind for info in MILESTONE_KIND_REGISTRY if info.kind in present]


def get_user_disabled_milestone_kinds(user: User) -> set[str]:
    """Kind'ы, которые пользователь скрыл у себя. Отсутствие = включено."""
    return set(normalize_disabled_kinds(user.history_disabled_kinds))


def get_user_disabled_milestone_kinds_by_id(db: Session, user_id: UUID) -> set[str]:
    raw = (
        db.query(User.history_disabled_kinds)
        .filter(User.id == user_id)
        .scalar()
    )
    return set(normalize_disabled_kinds(raw))


def list_user_milestone_kind_settings(user: User) -> list[dict[str, object]]:
    """Полный канонический список видов вех + персональное состояние enabled."""
    disabled = get_user_disabled_milestone_kinds(user)
    return [
        {
            "kind": info.kind,
            "label": info.label,
            "description": info.description,
            "enabled": info.kind not in disabled,
        }
        for info in MILESTONE_KIND_REGISTRY
    ]


def set_user_milestone_kind_enabled(user: User, kind: str, enabled: bool) -> dict[str, object]:
    if kind not in MILESTONE_KINDS:
        raise UnknownMilestoneKindError(kind)
    disabled = get_user_disabled_milestone_kinds(user)
    if enabled:
        disabled.discard(kind)
    else:
        disabled.add(kind)
    # Пересобираем в каноническом порядке — стабильный, читаемый JSONB.
    user.history_disabled_kinds = [
        info.kind for info in MILESTONE_KIND_REGISTRY if info.kind in disabled
    ]
    return {"kind": kind, "enabled": enabled}
