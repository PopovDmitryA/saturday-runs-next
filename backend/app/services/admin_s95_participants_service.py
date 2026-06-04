from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Participant, Platform


def search_admin_s95_participants(
    db: Session,
    *,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, object]], int]:
    platform = db.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        return [], 0

    base = db.query(Participant).filter(Participant.platform_id == platform.id)
    normalized = (query or "").strip()
    if normalized:
        like = f"%{normalized}%"
        base = base.filter(
            or_(
                Participant.external_user_id.ilike(like),
                Participant.display_name.ilike(like),
                Participant.barcode_id.ilike(like),
                Participant.planning_location.ilike(like),
            )
        )

    total = base.with_entities(func.count(Participant.id)).scalar() or 0
    rows = (
        base.order_by(Participant.fetched_at.desc().nullslast(), Participant.external_user_id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        {
            "id": str(row.id),
            "external_user_id": row.external_user_id,
            "display_name": row.display_name,
            "profile_url": row.profile_url,
            "barcode_id": row.barcode_id,
            "club_name": row.club_name,
            "planning_location": row.planning_location,
            "planning_location_seen_at": row.planning_location_seen_at,
            "fetched_at": row.fetched_at,
            "sync_status": row.sync_status.value,
            "error_message": row.error_message,
        }
        for row in rows
    ]
    return items, total
