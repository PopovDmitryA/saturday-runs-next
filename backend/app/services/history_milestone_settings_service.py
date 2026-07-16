from __future__ import annotations

from sqlalchemy.orm import Session

from app.history_milestone_kinds import MILESTONE_KIND_REGISTRY, MILESTONE_KINDS
from app.models import HistoryMilestoneSetting


class UnknownMilestoneKindError(Exception):
    pass


def get_disabled_milestone_kinds(db: Session) -> set[str]:
    """Kind'ы, явно выключенные админом. Отсутствие строки = включено."""
    rows = db.query(HistoryMilestoneSetting.kind).filter(HistoryMilestoneSetting.enabled.is_(False)).all()
    return {row[0] for row in rows}


def list_milestone_kind_settings(db: Session) -> list[dict[str, object]]:
    """Полный канонический список видов вех + текущее состояние enabled."""
    disabled = get_disabled_milestone_kinds(db)
    return [
        {
            "kind": info.kind,
            "label": info.label,
            "description": info.description,
            "enabled": info.kind not in disabled,
        }
        for info in MILESTONE_KIND_REGISTRY
    ]


def set_milestone_kind_enabled(db: Session, kind: str, enabled: bool) -> dict[str, object]:
    if kind not in MILESTONE_KINDS:
        raise UnknownMilestoneKindError(kind)
    row = db.query(HistoryMilestoneSetting).filter(HistoryMilestoneSetting.kind == kind).one_or_none()
    if row is None:
        row = HistoryMilestoneSetting(kind=kind, enabled=enabled)
        db.add(row)
    else:
        row.enabled = enabled
    db.commit()
    return {"kind": kind, "enabled": enabled}
