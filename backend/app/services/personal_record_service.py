from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Event, RunResult
from app.sync import upsert


def recalculate_personal_records(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
) -> dict[str, int]:
    """Mark is_pr when a run is faster than all earlier runs at the same location."""
    platform = upsert.get_platform(db, platform_code)
    query = (
        db.query(RunResult, Event)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            Event.platform_id == platform.id,
            RunResult.participant_id.isnot(None),
        )
    )
    if participant_id is not None:
        query = query.filter(RunResult.participant_id == participant_id)

    grouped: dict[tuple[UUID, UUID], list[tuple[RunResult, Event]]] = {}
    for run, event in query.all():
        key = (run.participant_id, event.location_id)
        grouped.setdefault(key, []).append((run, event))

    updated = 0
    pr_runs = 0
    for items in grouped.values():
        items.sort(key=lambda pair: (pair[1].event_date, pair[1].event_number or 0))
        best_time: int | None = None
        for run, _event in items:
            new_is_pr = False
            finish_time = run.finish_time_sec
            if finish_time is not None and finish_time > 0:
                if best_time is None or finish_time < best_time:
                    new_is_pr = True
                    best_time = finish_time
            if run.is_pr != new_is_pr:
                run.is_pr = new_is_pr
                updated += 1
            if new_is_pr:
                pr_runs += 1

    return {
        "platform_code": platform_code,
        "participants_touched": len(grouped),
        "runs_updated": updated,
        "pr_runs": pr_runs,
    }
