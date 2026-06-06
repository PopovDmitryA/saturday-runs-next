from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.services.personal_record_service import recalculate_personal_records

PLATFORM_CODE = "parkrun"


def recalculate_parkrun_personal_records(
    db: Session,
    *,
    participant_id: UUID | None = None,
) -> dict[str, int]:
    result = recalculate_personal_records(db, PLATFORM_CODE, participant_id=participant_id)
    return {
        "participants_touched": result["participants_touched"],
        "runs_updated": result["runs_updated"],
        "pr_runs": result["pr_runs"],
    }
