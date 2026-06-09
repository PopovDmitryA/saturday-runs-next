from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Platform, PlatformLink, RunResult
from app.sync import upsert

TIME_BASED_PR_PLATFORMS = frozenset({"parkrun", "s95", "five_verst"})


def reset_personal_records(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
) -> int:
    platform = upsert.get_platform(db, platform_code)
    event_ids = select(Event.id).where(Event.platform_id == platform.id)
    query = db.query(RunResult).filter(RunResult.event_id.in_(event_ids))
    if participant_id is not None:
        query = query.filter(RunResult.participant_id == participant_id)
    return query.update({RunResult.is_pr: False}, synchronize_session=False)


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
        reset_personal_records(db, platform_code, participant_id=participant_id)
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


def recalculate_participants_personal_records(
    db: Session,
    platform_code: str,
    participant_ids: set[UUID] | list[UUID],
) -> None:
    """Recalculate is_pr for specific participants (safe after profile/protocol sync)."""
    if platform_code not in TIME_BASED_PR_PLATFORMS:
        return
    for participant_id in set(participant_ids):
        recalculate_personal_records(db, platform_code, participant_id=participant_id)


def global_personal_record_run_ids(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> set[UUID]:
    """Run IDs where the athlete set a new all-systems best finish time (chronological)."""
    query = (
        db.query(RunResult.id, RunResult.finish_time_sec)
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .order_by(Event.event_date, Event.event_number, Event.location_id, RunResult.id)
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    global_best: int | None = None
    global_pr_ids: set[UUID] = set()
    for run_id, finish_time in query.all():
        if global_best is None or finish_time < global_best:
            global_pr_ids.add(run_id)
            global_best = finish_time
    return global_pr_ids
