from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Platform, SyncWatermark

# Timestamp through which all S95 protocols have been reconciled. Once s95.ru exposes
# updated_at on activities, incremental sync can request only changes after this point.
S95_PROTOCOLS_RECONCILED_THROUGH = "s95:protocols_reconciled_through"


def get_watermark(db: Session, platform: Platform, key: str) -> datetime | None:
    row = (
        db.query(SyncWatermark)
        .filter(SyncWatermark.platform_id == platform.id, SyncWatermark.key == key)
        .one_or_none()
    )
    return row.value_at if row is not None else None


def set_watermark(
    db: Session,
    platform: Platform,
    key: str,
    value_at: datetime,
    *,
    note: str | None = None,
) -> SyncWatermark:
    row = (
        db.query(SyncWatermark)
        .filter(SyncWatermark.platform_id == platform.id, SyncWatermark.key == key)
        .one_or_none()
    )
    if row is None:
        row = SyncWatermark(platform_id=platform.id, key=key, value_at=value_at, note=note)
        db.add(row)
    else:
        row.value_at = value_at
        if note is not None:
            row.note = note
    db.flush()
    return row
