from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, RunResult
from app.sync import upsert


def reset_personal_records(db: Session, platform_code: str) -> int:
    platform = upsert.get_platform(db, platform_code)
    event_ids = db.query(Event.id).filter(Event.platform_id == platform.id).subquery()
    return (
        db.query(RunResult)
        .filter(RunResult.event_id.in_(event_ids))
        .update({RunResult.is_pr: False}, synchronize_session=False)
    )


def recalculate_personal_records(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
    commit_every: int = 200,
    reset: bool = True,
) -> dict[str, int]:
    """Mark is_pr when a run beats the runner's previous best time at any location."""
    if reset:
        reset_personal_records(db, platform_code)
        db.flush()

    platform = upsert.get_platform(db, platform_code)
    participant_query = (
        db.query(RunResult.participant_id)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            Event.platform_id == platform.id,
            RunResult.participant_id.isnot(None),
        )
        .distinct()
    )
    if participant_id is not None:
        participant_query = participant_query.filter(RunResult.participant_id == participant_id)

    participant_ids = [row[0] for row in participant_query.all()]
    participants_touched = 0
    updated = 0
    pr_runs = 0

    for index, current_participant_id in enumerate(participant_ids, start=1):
        rows = (
            db.query(RunResult, Event)
            .join(Event, RunResult.event_id == Event.id)
            .filter(
                Event.platform_id == platform.id,
                RunResult.participant_id == current_participant_id,
            )
            .order_by(Event.event_date, Event.event_number, Event.location_id)
            .all()
        )
        if not rows:
            continue

        participants_touched += 1
        best_time: int | None = None
        for run, _event in rows:
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

        if commit_every > 0 and index % commit_every == 0:
            db.commit()

    return {
        "platform_code": platform_code,
        "participants_touched": participants_touched,
        "runs_updated": updated,
        "pr_runs": pr_runs,
    }
