from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import Event, EventCrosslink, Location, Platform, PlatformLink, RunResult
from app.services.location_catalog_service import LocationCatalogIndex
from app.sync import upsert

TIME_BASED_PR_PLATFORMS = frozenset({"parkrun", "s95", "five_verst", "runpark"})

# five_verst и HTML-протокол s95 уже проставляют is_first_run/is_first_run_at_location
# из иконок достижений на самом сайте — это авторитетный источник, трогать его не нужно.
# Для остальных путей синка (s95 JSON API, parkrun, runpark) поля никогда не заполняются,
# поэтому там флаг выводим сами: хронологически первый (не "не в зачёте") протокол
# участника на платформе / на локации.
FIRST_RUN_DERIVED_PLATFORMS = frozenset({"s95", "parkrun", "runpark"})


def _five_verst_protocol_personal_record(run: RunResult) -> bool:
    for label in run.achievement_labels or []:
        if "личный рекорд" in str(label).casefold():
            return True
    return False


def run_shows_personal_record(platform_code: str, run: RunResult) -> bool:
    if run.is_pr or run.is_first_run:
        return True
    if platform_code == "five_verst" and _five_verst_protocol_personal_record(run):
        return True
    return False


def run_is_personal_record_sql_filter():
    """SQL filter matching run_shows_personal_record (requires Platform join)."""
    five_verst_achievement_pr = and_(
        Platform.code == "five_verst",
        func.lower(cast(RunResult.achievement_labels, String)).like("%личный рекорд%"),
    )
    return or_(
        RunResult.is_pr.is_(True),
        RunResult.is_first_run.is_(True),
        five_verst_achievement_pr,
    )


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
    """Mark is_pr on time improvements, first platform run, and 5verst protocol PR labels."""
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

        # "Не в зачёте" duplicates (secondary crosslink events) must never carry a PR
        # marker — even inside their own system — because the same protocol is counted
        # on the primary platform. Force is_pr=False on them and exclude them from the
        # best-time / first-run computation so the PR lands on the counted run instead.
        row_event_ids = [event.id for _run, event in rows]
        secondary_event_ids: set[UUID] = set()
        if row_event_ids:
            secondary_event_ids = {
                cl_row[0]
                for cl_row in db.query(EventCrosslink.secondary_event_id)
                .filter(EventCrosslink.secondary_event_id.in_(row_event_ids))
                .all()
            }

        counted_rows = []
        for run, event in rows:
            if event.id in secondary_event_ids:
                if run.is_pr:
                    run.is_pr = False
                    updated += 1
                continue
            counted_rows.append((run, event))

        best_time: int | None = None
        for run_index, (run, _event) in enumerate(counted_rows):
            is_first_on_platform = run_index == 0
            time_based_pr = False
            finish_time = run.finish_time_sec
            if finish_time is not None and finish_time > 0:
                if best_time is None or finish_time < best_time:
                    time_based_pr = True
                    best_time = finish_time
            new_is_pr = time_based_pr or is_first_on_platform or run.is_first_run
            if platform.code == "five_verst" and _five_verst_protocol_personal_record(run):
                new_is_pr = True
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


def recalculate_first_run_flags(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
    commit_every: int = 200,
) -> dict[str, int]:
    """Derive is_first_run / is_first_run_at_location from chronological run order.

    For each participant on the platform, orders their (non "не в зачёте") runs by
    event_date/event_number/location_id: the first one is is_first_run=True, and the
    first run at each distinct location is is_first_run_at_location=True. Secondary
    crosslink duplicates never carry either flag.
    """
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

        row_event_ids = [event.id for _run, event in rows]
        secondary_event_ids: set[UUID] = set()
        if row_event_ids:
            secondary_event_ids = {
                cl_row[0]
                for cl_row in db.query(EventCrosslink.secondary_event_id)
                .filter(EventCrosslink.secondary_event_id.in_(row_event_ids))
                .all()
            }

        counted_rows = [(run, event) for run, event in rows if event.id not in secondary_event_ids]

        seen_locations: set[UUID] = set()
        for run_index, (run, event) in enumerate(counted_rows):
            new_is_first_run = run_index == 0
            new_is_first_run_at_location = event.location_id not in seen_locations
            seen_locations.add(event.location_id)
            if run.is_first_run != new_is_first_run:
                run.is_first_run = new_is_first_run
                updated += 1
            if run.is_first_run_at_location != new_is_first_run_at_location:
                run.is_first_run_at_location = new_is_first_run_at_location
                updated += 1

        for run, event in rows:
            if event.id in secondary_event_ids and (run.is_first_run or run.is_first_run_at_location):
                run.is_first_run = False
                run.is_first_run_at_location = False
                updated += 1

        if commit_every > 0 and index % commit_every == 0:
            db.commit()

    return {
        "platform_code": platform_code,
        "participants_touched": participants_touched,
        "runs_updated": updated,
    }


def recalculate_participants_first_run_flags(
    db: Session,
    platform_code: str,
    participant_ids: set[UUID] | list[UUID],
) -> None:
    """Recalculate first-run flags for specific participants (safe after protocol sync)."""
    if platform_code not in FIRST_RUN_DERIVED_PLATFORMS:
        return
    for participant_id in set(participant_ids):
        recalculate_first_run_flags(db, platform_code, participant_id=participant_id)


def user_secondary_crosslinked_run_ids(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> set[UUID]:
    """Run IDs that are duplicates via event_crosslinks: the user's run sits in a
    secondary crosslink event AND the user also has a run in the primary event (i.e. the
    "не в зачёте" runs shown in the runs list). These must not participate in any personal
    record calculation — neither the per-system PR nor the global all-systems record."""
    primary_run_sq = (
        db.query(RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(Event.id == EventCrosslink.primary_event_id)
        .correlate(EventCrosslink)
        .exists()
    )
    query = (
        db.query(RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(EventCrosslink, EventCrosslink.secondary_event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(primary_run_sq)
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))
    return {row[0] for row in query.distinct().all()}


def reset_cross_platform_personal_records(db: Session, user_id: UUID) -> int:
    run_ids = (
        db.query(RunResult.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
    )
    return (
        db.query(RunResult)
        .filter(RunResult.id.in_(run_ids))
        .update(
            {RunResult.is_global_pr: False, RunResult.is_location_pr: False},
            synchronize_session=False,
        )
    )


def recalculate_cross_platform_personal_records(
    db: Session,
    user_id: UUID,
    *,
    catalog_index: LocationCatalogIndex | None = None,
    include_test_events: bool = False,
    reset: bool = True,
) -> dict[str, int]:
    """Mark is_global_pr (all-time best finish across every platform) and
    is_location_pr (best finish at one physical location across platforms,
    chronologically — a location's first-ever run is never a location PR,
    only a later run that beats the standing best time is) from run order.

    Both records are athlete-wide (span every platform the user has linked),
    unlike is_pr which only compares within a single platform — hence this
    operates on user_id rather than participant_id."""
    if reset:
        reset_cross_platform_personal_records(db, user_id)
        db.flush()

    excluded_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    query = (
        db.query(RunResult, Event, Location, Platform)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
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
    if excluded_ids:
        query = query.filter(RunResult.id.notin_(excluded_ids))

    index = catalog_index or LocationCatalogIndex(db)
    global_best: int | None = None
    best_by_location: dict[str, int] = {}
    updated = 0
    global_pr_runs = 0
    location_pr_runs = 0

    for run, _event, location, platform in query.all():
        finish_time = run.finish_time_sec

        is_global_pr = global_best is None or finish_time < global_best
        if is_global_pr:
            global_best = finish_time

        key = index.canonical_identity_key(location, platform.code)
        previous_location_best = best_by_location.get(key)
        is_location_pr = previous_location_best is not None and finish_time < previous_location_best
        if previous_location_best is None or finish_time < previous_location_best:
            best_by_location[key] = finish_time

        if run.is_global_pr != is_global_pr or run.is_location_pr != is_location_pr:
            run.is_global_pr = is_global_pr
            run.is_location_pr = is_location_pr
            updated += 1
        if is_global_pr:
            global_pr_runs += 1
        if is_location_pr:
            location_pr_runs += 1

    return {
        "runs_updated": updated,
        "global_pr_runs": global_pr_runs,
        "location_pr_runs": location_pr_runs,
    }


def recalculate_participants_cross_platform_personal_records(
    db: Session,
    participant_ids: set[UUID] | list[UUID],
) -> None:
    """Recalculate is_global_pr/is_location_pr for the users linked to these
    participants. Safe to call after any single-platform sync: both records are
    athlete-wide, so a change on one platform can only be reflected correctly by
    recomputing across all of the affected users' linked platforms."""
    ids = set(participant_ids)
    if not ids:
        return
    user_ids = {
        row[0]
        for row in db.query(PlatformLink.user_id)
        .filter(PlatformLink.participant_id.in_(ids))
        .distinct()
        .all()
    }
    if not user_ids:
        return
    catalog_index = LocationCatalogIndex(db)
    for uid in user_ids:
        recalculate_cross_platform_personal_records(db, uid, catalog_index=catalog_index)
