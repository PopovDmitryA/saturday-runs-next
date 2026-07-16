from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.activity_date import has_real_activity_date
from app.activity_url import resolve_activity_url
from app.models import (
    DashboardCache,
    Event,
    EventCrosslink,
    EventSummary,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    SyncJob,
    SyncJobStatus,
    SyncJobTrigger,
    User,
    VolunteerResult,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.location_map_service import _location_is_cancelled, _location_is_paused
from app.services.sync_error_format import present_sync_error
from app.services.user_location_stats import count_unique_geo_from_rows, count_unique_locations_from_rows
from app.services.user_unique_locations_detail import _platform_sort_key
from app.time_format import normalize_finish_time_display
from app.volunteering_occasions import (
    count_volunteering_for_platform,
    count_volunteering_occasions,
    volunteer_occasion_dates,
    volunteering_by_month_for_platform,
)


class SyncRefreshRateLimitedError(Exception):
    pass


ANALYTICS_VERSION = 24

RUN_MILESTONES = (10, 25, 50, 100, 250, 500, 1000)
RUN_CLUBS = (50, 100, 250, 500, 1000)
DISTANCE_KM_PER_RUN = 5


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(value: date, delta: int) -> date:
    month_index = value.year * 12 + (value.month - 1) + delta
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def _last_n_month_keys(today: date, months: int = 12) -> list[str]:
    current = _month_start(today)
    return [f"{_add_months(current, offset).year}-{_add_months(current, offset).month:02d}" for offset in range(-(months - 1), 1)]


def _month_key(value: date) -> str:
    return f"{value.year}-{value.month:02d}"


def _format_volunteering_index(runs: int, volunteering: int) -> str | None:
    if runs == 0 and volunteering == 0:
        return None
    if runs == 0:
        return "100%+"
    ratio = volunteering / runs
    if ratio > 1:
        return "100%+"
    return f"{round(ratio * 100)}%"


def _week_saturday(value: date) -> date:
    """Saturday closing the Sunday-Saturday week that contains the given date.

    Календарь суббот считает по неделям, а не по буквальной дате старта: если
    в одну и ту же неделю пришлось два старта (та же суббота двумя протоколами
    или, например, суббота + внеплановый будний старт), это одна и та же
    неделя — засчитываем её один раз. Совпадает с weekSaturday() во
    фронтенд-виджете (ActivityCalendarHeatmap.tsx).
    """
    return value + timedelta(days=(5 - value.weekday()) % 7)


def _saturday_streak(activity_dates: set[date]) -> int:
    saturdays = {_week_saturday(value) for value in activity_dates}
    if not saturdays:
        return 0
    streak = 0
    expected = max(saturdays)
    while expected in saturdays:
        streak += 1
        expected -= timedelta(days=7)
    return streak


def _current_saturday_streak(activity_dates: set[date], today: date) -> int:
    """Streak that is alive *now*: counts back from the most recent past Saturday.
    Unlike _saturday_streak (which counts from the last active Saturday whenever it
    was), a missed last Saturday breaks the streak. The very last Saturday is allowed
    to be missing for one week — protocols are often synced with a delay."""
    saturdays = {_week_saturday(value) for value in activity_dates}
    if not saturdays:
        return 0
    last_saturday = today - timedelta(days=(today.weekday() - 5) % 7)
    expected = last_saturday
    if expected not in saturdays:
        expected -= timedelta(days=7)
    streak = 0
    while expected in saturdays:
        streak += 1
        expected -= timedelta(days=7)
    return streak


def _max_saturday_streak(activity_dates: set[date]) -> int:
    saturdays = sorted({_week_saturday(value) for value in activity_dates})
    best = 0
    current = 0
    previous: date | None = None
    for value in saturdays:
        current = current + 1 if previous is not None and value - previous == timedelta(days=7) else 1
        best = max(best, current)
        previous = value
    return best


def _saturdays_in_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() == 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _saturday_consistency(activity_dates: set[date], today: date) -> tuple[float | None, int, int]:
    window_start = today - timedelta(days=364)
    saturdays = _saturdays_in_range(window_start, today)
    if not saturdays:
        return None, 0, 0
    active_weeks = {_week_saturday(value) for value in activity_dates}
    active = sum(1 for value in saturdays if value in active_weeks)
    pct = round(active / len(saturdays) * 100, 1)
    return pct, active, len(saturdays)


def _next_run_milestone(total_runs: int) -> tuple[int | None, int | None]:
    for milestone in RUN_MILESTONES:
        if total_runs < milestone:
            return milestone, milestone - total_runs
    return None, None


def _earned_run_clubs(total_runs: int) -> list[int]:
    return [milestone for milestone in RUN_CLUBS if total_runs >= milestone]


def _next_run_club(total_runs: int) -> int | None:
    for milestone in RUN_CLUBS:
        if total_runs < milestone:
            return milestone
    return None


def _first_visits_by_catalog_key(
    catalog_index: LocationCatalogIndex,
    visit_rows: list[tuple[date, Location, str]],
) -> dict[str, date]:
    first_visits: dict[str, date] = {}
    for event_date, location, platform_code in visit_rows:
        key = catalog_index.canonical_identity_key(location, platform_code)
        current = first_visits.get(key)
        if current is None or event_date < current:
            first_visits[key] = event_date
    return first_visits


def _count_volunteering_in_period(
    vol_rows_by_platform: dict[str, list[tuple[date, str]]],
    *,
    since: date,
) -> int:
    total = 0
    for platform_code, rows in vol_rows_by_platform.items():
        filtered = [(event_date, location_key) for event_date, location_key in rows if event_date >= since]
        total += count_volunteering_for_platform(platform_code, filtered)
    return total


def _avg_vs_field_pct(comparison_rows: list[tuple[int, int]]) -> float | None:
    if not comparison_rows:
        return None
    deltas = [(field_avg - user_time) / field_avg * 100 for user_time, field_avg in comparison_rows if field_avg > 0]
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 1)


def _resolve_field_avg_sec(
    summary_avg: object | None,
    computed_avg: object | None,
    runner_count: object | None,
    *,
    min_computed_runners: int = 2,
) -> int | None:
    summary_value = _to_int(summary_avg)
    if summary_value is not None and summary_value > 0:
        return summary_value
    if computed_avg is None or runner_count is None:
        return None
    count = int(runner_count)
    if count < min_computed_runners:
        return None
    computed_value = _to_int(computed_avg)
    if computed_value is None or computed_value <= 0:
        return None
    return computed_value


def _collect_field_comparison_pairs(
    rows: list[tuple[object | None, object | None, object | None, object | None]],
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for user_time, summary_avg, computed_avg, runner_count in rows:
        user_value = _to_int(user_time)
        field_avg = _resolve_field_avg_sec(summary_avg, computed_avg, runner_count)
        if user_value is None or field_avg is None:
            continue
        pairs.append((user_value, field_avg))
    return pairs


def _location_field_avg_subquery(db: Session):
    return (
        db.query(
            Event.location_id.label("location_id"),
            Event.event_date.label("event_date"),
            func.avg(RunResult.finish_time_sec).label("avg_time_sec"),
            func.count(RunResult.id).label("runner_count"),
        )
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(RunResult.finish_time_sec.isnot(None))
        .group_by(Event.location_id, Event.event_date)
        .subquery()
    )


def _to_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(round(value))
    return int(round(float(value)))


def _to_float(value: object | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_latest_sync_job(db: Session, user_id: UUID) -> SyncJob | None:
    return (
        db.query(SyncJob)
        .filter(SyncJob.user_id == user_id)
        .order_by(SyncJob.created_at.desc())
        .limit(1)
        .one_or_none()
    )


def _count_secondary_crosslinked_results(
    db: Session,
    participant_id: UUID,
    user_id: UUID,
    *,
    include_test_events: bool,
) -> tuple[int, int]:
    """Count runs/volunteering that are in secondary crosslink events AND the user
    also has a run/vol in the corresponding primary event.
    Returns (duplicate_runs, duplicate_vols)."""
    # Primary-event subquery: any run/vol by any of user's participants in the primary event
    primary_run_sq = (
        db.query(RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(Event.id == EventCrosslink.primary_event_id)
        .correlate(EventCrosslink)
        .exists()
    )
    base_run = (
        db.query(func.count(RunResult.id))
        .join(Event, RunResult.event_id == Event.id)
        .join(EventCrosslink, EventCrosslink.secondary_event_id == Event.id)
        .filter(RunResult.participant_id == participant_id)
        .filter(primary_run_sq)
    )
    primary_vol_sq = (
        db.query(VolunteerResult.id)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(Event.id == EventCrosslink.primary_event_id)
        .correlate(EventCrosslink)
        .exists()
    )
    base_vol = (
        db.query(func.count(VolunteerResult.id))
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(EventCrosslink, EventCrosslink.secondary_event_id == Event.id)
        .filter(VolunteerResult.participant_id == participant_id)
        .filter(primary_vol_sq)
    )
    if not include_test_events:
        base_run = base_run.filter(Event.is_test_event.is_(False))
        base_vol = base_vol.filter(Event.is_test_event.is_(False))
    return (base_run.scalar() or 0), (base_vol.scalar() or 0)


def compute_dashboard_stats(db: Session, user_id: UUID, *, include_test_events: bool = False) -> dict[str, object]:
    links = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )

    by_platform: dict[str, dict[str, int]] = {}
    total_runs = 0
    total_volunteering = 0

    for link, platform in links:
        if link.participant_id is None:
            by_platform[platform.code] = {"runs": 0, "volunteering": 0}
            continue

        runs_query = (
            db.query(func.count(RunResult.id))
            .join(Event, RunResult.event_id == Event.id)
            .filter(
                RunResult.participant_id == link.participant_id,
                Event.platform_id == platform.id,
            )
        )
        vol_query = (
            db.query(func.count(VolunteerResult.id))
            .join(Event, VolunteerResult.event_id == Event.id)
            .filter(
                VolunteerResult.participant_id == link.participant_id,
                Event.platform_id == platform.id,
            )
        )
        vol_rows_query = (
            db.query(Event.event_date, Location.external_key)
            .select_from(VolunteerResult)
            .join(Event, VolunteerResult.event_id == Event.id)
            .join(Location, Event.location_id == Location.id)
            .filter(
                VolunteerResult.participant_id == link.participant_id,
                Event.platform_id == platform.id,
                Event.event_date > date(1970, 1, 1),
            )
        )
        if not include_test_events:
            runs_query = runs_query.filter(Event.is_test_event.is_(False))
            vol_query = vol_query.filter(Event.is_test_event.is_(False))
            vol_rows_query = vol_rows_query.filter(Event.is_test_event.is_(False))

        runs_count = runs_query.scalar() or 0
        if platform.code == "five_verst":
            vol_count = count_volunteering_occasions(
                [
                    (event_date, location_key or "unknown")
                    for event_date, location_key in vol_rows_query.all()
                ]
            )
        elif platform.code == "parkrun" and link.participant_id is not None:
            participant = (
                db.query(Participant).filter(Participant.id == link.participant_id).one_or_none()
            )
            if participant is None:
                vol_count = 0
            else:
                from app.parkrun.volunteer_credits import count_parkrun_volunteering

                vol_count = count_parkrun_volunteering(db, participant, platform.id)
        else:
            vol_count = vol_query.scalar() or 0
        by_platform[platform.code] = {"runs": runs_count, "volunteering": vol_count}
        total_runs += runs_count
        total_volunteering += vol_count

    # Dedup: subtract results that are secondary in event_crosslinks,
    # but only if the user also has a run in the corresponding primary event.
    # by_platform keeps gross counts; total_* are deduplicated.
    for link, _platform in links:
        if link.participant_id is None:
            continue
        dup_runs, dup_vols = _count_secondary_crosslinked_results(
            db, link.participant_id, user_id, include_test_events=include_test_events
        )
        if dup_runs or dup_vols:
            total_runs = max(0, total_runs - dup_runs)
            total_volunteering = max(0, total_volunteering - dup_vols)

    analytics = _compute_dashboard_analytics(
        db,
        user_id,
        total_runs=total_runs,
        total_volunteering=total_volunteering,
        by_platform=by_platform,
        include_test_events=include_test_events,
    )

    return {
        "total_runs": total_runs,
        "total_volunteering": total_volunteering,
        "by_platform": by_platform,
        "analytics": analytics,
    }


def _compute_dashboard_analytics(
    db: Session,
    user_id: UUID,
    *,
    total_runs: int,
    total_volunteering: int,
    by_platform: dict[str, dict[str, int]],
    include_test_events: bool = False,
) -> dict[str, object]:
    from app.services.personal_record_service import (
        run_is_personal_record_sql_filter,
        user_secondary_crosslinked_run_ids,
    )

    today = date.today()
    twelve_months_ago = today - timedelta(days=365)
    year_start = date(today.year, 1, 1)

    runs_query = (
        db.query(RunResult, Event, Location, Platform)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
        )
    )
    vol_query = (
        db.query(VolunteerResult, Event, Location, Platform)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        runs_query = runs_query.filter(Event.is_test_event.is_(False))
        vol_query = vol_query.filter(Event.is_test_event.is_(False))

    # Кросслинк-дубли (RunPark republish "не в зачёте", см. list_co_runners)
    # исключаем ДО построения run_location_rows — иначе локация/город, где
    # у юзера есть только такой дубль, попадает в счётчик плитки, но
    # отсутствует в детализации (build_user_unique_location_details уже
    # фильтрует их так же), и числа расходятся.
    secondary_crosslinked_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    if secondary_crosslinked_ids:
        runs_query = runs_query.filter(RunResult.id.notin_(secondary_crosslinked_ids))

    run_location_rows = runs_query.with_entities(Location, Platform.code).distinct().all()
    vol_location_rows = vol_query.with_entities(Location, Platform.code).distinct().all()

    catalog_index = LocationCatalogIndex(db)

    run_unique_counts = count_unique_locations_from_rows(catalog_index, run_location_rows)
    vol_unique_counts = count_unique_locations_from_rows(catalog_index, vol_location_rows)
    all_unique_counts = count_unique_locations_from_rows(
        catalog_index,
        run_location_rows + vol_location_rows,
    )
    unique_run_regions, unique_run_cities = count_unique_geo_from_rows(catalog_index, run_location_rows)
    unique_volunteer_regions, unique_volunteer_cities = count_unique_geo_from_rows(catalog_index, vol_location_rows)

    timed_runs = runs_query.filter(RunResult.finish_time_sec.isnot(None))
    paced_runs = runs_query.filter(RunResult.pace_sec_per_km.isnot(None))
    positioned_runs = runs_query.filter(RunResult.position.isnot(None))

    finish_times_sec = sorted(
        int(row[0]) for row in timed_runs.with_entities(RunResult.finish_time_sec).all()
    )
    avg_finish = timed_runs.with_entities(func.avg(RunResult.finish_time_sec)).scalar()
    best_finish = timed_runs.with_entities(func.min(RunResult.finish_time_sec)).scalar()
    best_results_platform_count = timed_runs.with_entities(Platform.code).distinct().count()
    avg_pace = paced_runs.with_entities(func.avg(RunResult.pace_sec_per_km)).scalar()
    avg_position = positioned_runs.with_entities(func.avg(RunResult.position)).scalar()
    avg_gender_position = (
        runs_query.filter(RunResult.gender_position.isnot(None))
        .with_entities(func.avg(RunResult.gender_position))
        .scalar()
    )
    pr_count = (
        runs_query.filter(run_is_personal_record_sql_filter())
        .with_entities(func.count(RunResult.id))
        .scalar()
        or 0
    )
    last_pr_date = (
        runs_query.filter(run_is_personal_record_sql_filter())
        .with_entities(func.max(Event.event_date))
        .scalar()
    )

    last_global_pr_date = (
        runs_query.filter(RunResult.is_global_pr.is_(True))
        .with_entities(func.max(Event.event_date))
        .scalar()
    )
    pr_last_12_months = (
        runs_query.filter(run_is_personal_record_sql_filter(), Event.event_date >= twelve_months_ago)
        .with_entities(func.count(RunResult.id))
        .scalar()
        or 0
    )

    location_field_avg = _location_field_avg_subquery(db)
    field_comparison_rows = (
        timed_runs.outerjoin(EventSummary, EventSummary.event_id == Event.id)
        .outerjoin(
            location_field_avg,
            (Event.location_id == location_field_avg.c.location_id)
            & (Event.event_date == location_field_avg.c.event_date),
        )
        .with_entities(
            RunResult.finish_time_sec,
            EventSummary.avg_time_sec,
            location_field_avg.c.avg_time_sec,
            location_field_avg.c.runner_count,
        )
        .all()
    )
    field_comparison_pairs = _collect_field_comparison_pairs(field_comparison_rows)
    avg_vs_field_pct = _avg_vs_field_pct(field_comparison_pairs)
    runs_with_field_avg_count = len(field_comparison_pairs)

    first_run = runs_query.with_entities(func.min(Event.event_date)).scalar()
    last_run = runs_query.with_entities(func.max(Event.event_date)).scalar()
    first_vol = vol_query.with_entities(func.min(Event.event_date)).scalar()
    last_vol = vol_query.with_entities(func.max(Event.event_date)).scalar()

    activity_dates = [
        d for d in (first_run, first_vol, last_run, last_vol) if has_real_activity_date(d)
    ]
    first_activity = min(activity_dates) if activity_dates else None
    last_activity = max(activity_dates) if activity_dates else None

    role_rows = (
        vol_query.filter(VolunteerResult.role.isnot(None), VolunteerResult.role != "")
        .with_entities(VolunteerResult.role)
        .distinct()
        .all()
    )
    unique_volunteer_roles = len({row[0].strip() for row in role_rows if row[0] and row[0].strip()})

    run_dates_for_top_location = runs_query.with_entities(Event.event_date, Location, Platform.code).all()
    top_location_dates: dict[str, set[date]] = defaultdict(set)
    top_location_platform_codes: dict[str, set[str]] = defaultdict(set)
    top_location_sample: dict[str, tuple[Location, str]] = {}
    for event_date, location, platform_code in run_dates_for_top_location:
        identity_key = catalog_index.canonical_identity_key(location, platform_code)
        top_location_dates[identity_key].add(event_date)
        top_location_platform_codes[identity_key].add(platform_code)
        top_location_sample.setdefault(identity_key, (location, platform_code))

    top_location = None
    if top_location_dates:
        top_identity_key = sorted(
            top_location_dates,
            key=lambda key: (
                -len(top_location_dates[key]),
                catalog_index.display_name(*top_location_sample[key]),
            ),
        )[0]
        max_count = len(top_location_dates[top_identity_key])
        tied_count = sum(1 for dates in top_location_dates.values() if len(dates) == max_count)
        catalog = catalog_index.get_for_identity_key(top_identity_key)
        display_name = (
            catalog.canonical_name
            if catalog is not None and catalog.canonical_name
            else catalog_index.display_name(*top_location_sample[top_identity_key])
        )
        top_location = {
            "name": display_name,
            "platform_codes": sorted(top_location_platform_codes[top_identity_key], key=_platform_sort_key),
            "count": max_count,
            "tied_count": tied_count,
        }

    top_role_row = (
        vol_query.filter(VolunteerResult.role.isnot(None), VolunteerResult.role != "")
        .with_entities(VolunteerResult.role, func.count(VolunteerResult.id).label("role_count"))
        .group_by(VolunteerResult.role)
        .order_by(func.count(VolunteerResult.id).desc(), VolunteerResult.role.asc())
        .limit(1)
        .one_or_none()
    )
    top_volunteer_role = None
    if top_role_row is not None:
        top_volunteer_role = {"role": top_role_row[0].strip(), "count": int(top_role_row[1])}

    runs_last_12_months = (
        runs_query.filter(Event.event_date >= twelve_months_ago).with_entities(func.count(RunResult.id)).scalar() or 0
    )
    runs_current_year = (
        runs_query.filter(Event.event_date >= year_start).with_entities(func.count(RunResult.id)).scalar() or 0
    )

    platform_metric_rows = (
        runs_query.with_entities(
            Platform.code,
            func.avg(RunResult.finish_time_sec),
            func.avg(RunResult.pace_sec_per_km),
        )
        .group_by(Platform.code)
        .order_by(Platform.code.asc())
        .all()
    )
    platform_metrics = []
    for platform_code, platform_avg_finish, platform_avg_pace in platform_metric_rows:
        if platform_avg_finish is None and platform_avg_pace is None:
            continue
        platform_metrics.append(
            {
                "platform_code": platform_code,
                "avg_finish_time_sec": _to_int(platform_avg_finish),
                "avg_pace_sec_per_km": _to_int(platform_avg_pace),
            }
        )

    run_dates = [row[0] for row in runs_query.with_entities(Event.event_date).all()]
    vol_month_rows = vol_query.with_entities(Event.event_date, Location.external_key, Platform.code).all()
    vol_rows_by_platform: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for event_date, location_key, platform_code in vol_month_rows:
        vol_rows_by_platform[platform_code].append((event_date, location_key or "unknown"))
    vols_by_month: Counter[str] = Counter()
    for platform_code, rows in vol_rows_by_platform.items():
        vols_by_month.update(volunteering_by_month_for_platform(platform_code, rows))
    run_activity_dates = set(run_dates)
    vol_activity_dates = {
        event_date
        for platform_code, rows in vol_rows_by_platform.items()
        for event_date in volunteer_occasion_dates(platform_code, rows)
    }
    all_activity_dates = run_activity_dates | vol_activity_dates

    run_visit_rows = runs_query.with_entities(Event.event_date, Location, Platform.code).all()
    vol_visit_rows = vol_query.with_entities(Event.event_date, Location, Platform.code).all()

    runs_per_date: Counter[date] = Counter(d for d in run_dates if has_real_activity_date(d))
    vols_per_date: Counter[date] = Counter()
    for platform_code, rows in vol_rows_by_platform.items():
        vols_per_date.update(volunteer_occasion_dates(platform_code, rows))

    run_items_by_date: dict[date, list[dict[str, str]]] = defaultdict(list)
    for event_date, location, platform_code in run_visit_rows:
        if has_real_activity_date(event_date):
            run_items_by_date[event_date].append(
                {
                    "platform_code": platform_code,
                    "location": catalog_index.display_name(location, platform_code),
                }
            )
    vol_items_by_date: dict[date, list[dict[str, str]]] = defaultdict(list)
    seen_vol_items: set[tuple[date, str, str]] = set()
    for event_date, location, platform_code in vol_visit_rows:
        display_name = catalog_index.display_name(location, platform_code)
        dedup_key = (event_date, platform_code, display_name)
        if dedup_key in seen_vol_items:
            continue
        seen_vol_items.add(dedup_key)
        vol_items_by_date[event_date].append(
            {"platform_code": platform_code, "location": display_name}
        )

    activity_calendar = [
        {
            "date": value.isoformat(),
            "runs": int(runs_per_date.get(value, 0)),
            "volunteering": int(vols_per_date.get(value, 0)),
            "run_items": run_items_by_date.get(value, []),
            "volunteer_items": vol_items_by_date.get(value, []),
        }
        for value in sorted(set(runs_per_date) | set(vols_per_date))
    ]

    run_month_rows = runs_query.with_entities(Event.event_date, RunResult.pace_sec_per_km).all()
    runs_by_month: Counter[str] = Counter(_month_key(row[0]) for row in run_month_rows)
    pace_by_month: dict[str, list[int]] = defaultdict(list)
    for event_date, pace_sec in run_month_rows:
        if pace_sec is not None:
            pace_by_month[_month_key(event_date)].append(int(pace_sec))

    month_keys = _last_n_month_keys(today)
    activity_by_month = [
        {
            "month": month,
            "runs": int(runs_by_month.get(month, 0)),
            "volunteering": int(vols_by_month.get(month, 0)),
        }
        for month in month_keys
    ]
    pace_trend = []
    for month in month_keys:
        pace_values = pace_by_month.get(month, [])
        if not pace_values:
            continue
        pace_trend.append(
            {
                "month": month,
                "avg_pace_sec_per_km": round(sum(pace_values) / len(pace_values)),
            }
        )

    volunteering_last_12_months = _count_volunteering_in_period(
        vol_rows_by_platform,
        since=twelve_months_ago,
    )
    volunteering_current_year = _count_volunteering_in_period(
        vol_rows_by_platform,
        since=year_start,
    )

    visit_rows = list(run_visit_rows) + list(vol_visit_rows)
    first_visits = _first_visits_by_catalog_key(catalog_index, visit_rows)
    new_locations_last_12_months = sum(
        1 for first_visit in first_visits.values() if first_visit >= twelve_months_ago
    )

    saturday_consistency_pct, saturday_consistency_active, saturday_consistency_total = _saturday_consistency(
        all_activity_dates,
        today,
    )
    next_milestone_runs, runs_to_next_milestone = _next_run_milestone(total_runs)
    run_clubs_earned = _earned_run_clubs(total_runs)
    next_run_club = _next_run_club(total_runs)
    total_distance_km = total_runs * DISTANCE_KM_PER_RUN

    return {
        "analytics_version": ANALYTICS_VERSION,
        "unique_locations": all_unique_counts.unique_total,
        "unique_run_locations": run_unique_counts.unique_total,
        "unique_run_regions": unique_run_regions,
        "unique_run_cities": unique_run_cities,
        "unique_volunteer_locations": vol_unique_counts.unique_total,
        "unique_volunteer_regions": unique_volunteer_regions,
        "unique_volunteer_cities": unique_volunteer_cities,
        "avg_finish_time_sec": _to_int(avg_finish),
        "best_finish_time_sec": _to_int(best_finish),
        "best_results_platform_count": int(best_results_platform_count),
        "avg_pace_sec_per_km": _to_int(avg_pace),
        "avg_position": _to_float(avg_position),
        "avg_gender_position": _to_float(avg_gender_position),
        "pr_count": pr_count,
        "unique_volunteer_roles": unique_volunteer_roles,
        "first_activity_date": first_activity.isoformat() if first_activity else None,
        "last_activity_date": last_activity.isoformat() if last_activity else None,
        "first_run_date": first_run.isoformat() if first_run else None,
        "days_since_first_run": (today - first_run).days if first_run is not None else None,
        "top_location": top_location,
        "top_volunteer_role": top_volunteer_role,
        "runs_last_12_months": runs_last_12_months,
        "runs_current_year": runs_current_year,
        "volunteering_index": _format_volunteering_index(total_runs, total_volunteering),
        "saturday_streak": _saturday_streak(all_activity_dates),
        "saturday_streak_max": _max_saturday_streak(all_activity_dates),
        "saturday_run_streak_max": _max_saturday_streak(run_activity_dates),
        "saturday_vol_streak_max": _max_saturday_streak(vol_activity_dates),
        "saturday_streak_current": _current_saturday_streak(all_activity_dates, today),
        "saturday_run_streak_current": _current_saturday_streak(run_activity_dates, today),
        "saturday_vol_streak_current": _current_saturday_streak(vol_activity_dates, today),
        "activity_calendar": activity_calendar,
        "finish_times_sec": finish_times_sec,
        "platform_metrics": platform_metrics,
        "activity_by_month": activity_by_month,
        "pace_trend": pace_trend,
        "volunteering_last_12_months": volunteering_last_12_months,
        "volunteering_current_year": volunteering_current_year,
        "total_distance_km": total_distance_km,
        "next_milestone_runs": next_milestone_runs,
        "runs_to_next_milestone": runs_to_next_milestone,
        "saturday_consistency_pct": saturday_consistency_pct,
        "saturday_consistency_active": saturday_consistency_active,
        "saturday_consistency_total": saturday_consistency_total,
        "last_pr_date": last_pr_date.isoformat() if last_pr_date else None,
        "last_global_pr_date": last_global_pr_date.isoformat() if last_global_pr_date else None,
        "pr_last_12_months": pr_last_12_months,
        "new_locations_last_12_months": new_locations_last_12_months,
        "run_clubs_earned": run_clubs_earned,
        "next_run_club": next_run_club,
        "avg_vs_field_pct": avg_vs_field_pct,
        "runs_with_field_avg_count": runs_with_field_avg_count,
    }


def invalidate_dashboard_cache_for_platform(db: Session, platform_code: str) -> int:
    """Delete dashboard_cache for all users linked to the given platform.

    Called after bulk sync that upserted new run results so the cache is
    lazily recomputed on the next profile page load.
    """
    platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        return 0
    user_ids = db.query(PlatformLink.user_id).filter(PlatformLink.platform_id == platform.id).subquery()
    deleted = db.query(DashboardCache).filter(DashboardCache.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.flush()
    return deleted


def recompute_dashboard_cache(db: Session, user_id: UUID) -> DashboardCache:
    stats = compute_dashboard_stats(db, user_id)
    row = db.query(DashboardCache).filter(DashboardCache.user_id == user_id).one_or_none()
    now = _utcnow()
    if row is None:
        row = DashboardCache(user_id=user_id, computed_at=now, stats=stats)
        db.add(row)
    else:
        row.computed_at = now
        row.stats = stats
    db.flush()
    return row


def _activity_event_url(
    *,
    platform_code: str,
    event: Event,
    location: Location,
    profile_url: str | None,
    summary_source_url: str | None = None,
) -> str | None:
    return resolve_activity_url(
        platform_code=platform_code,
        event_date=event.event_date,
        event_number=event.event_number,
        event_source_url=event.source_url,
        location_external_key=location.external_key,
        profile_url=profile_url,
        summary_source_url=summary_source_url,
    )


def _event_summary_source_urls(
    db: Session,
    events: list[Event],
) -> dict[tuple[UUID, UUID, date], str | None]:
    from sqlalchemy import tuple_

    keys = {(event.platform_id, event.location_id, event.event_date) for event in events}
    if not keys:
        return {}
    rows = (
        db.query(EventSummary)
        .filter(
            tuple_(EventSummary.platform_id, EventSummary.location_id, EventSummary.event_date).in_(keys)
        )
        .all()
    )
    return {(row.platform_id, row.location_id, row.event_date): row.source_url for row in rows}


def _location_status_fields(
    catalog_index: LocationCatalogIndex,
    location: Location,
    platform_code: str,
) -> dict[str, bool]:
    return {
        "location_is_paused": _location_is_paused(location, catalog_index, platform_code),
        "location_is_cancelled": _location_is_cancelled(location),
    }


def list_user_runs(
    db: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    include_test_events: bool = False,
) -> list[dict[str, object]]:
    query = (
        db.query(RunResult, Event, Location, Platform, PlatformLink)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))
    rows = query.order_by(Event.event_date.desc(), RunResult.position.asc()).offset(offset).limit(limit).all()

    from app.services.personal_record_service import (
        run_shows_personal_record,
        user_secondary_crosslinked_run_ids,
    )

    # "Не в зачёте" — только если у самого юзера есть и secondary (этот забег), и
    # primary результат (см. user_secondary_crosslinked_run_ids). Событие может быть
    # кросслинкнуто целиком (dual_load локация), но если юзер лично бежал только в
    # одной из систем в этот день — у него нет дубля, забег зачётный.
    secondary_crosslinked_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    catalog_index = LocationCatalogIndex(db)
    summary_urls = _event_summary_source_urls(db, [event for _run, event, _loc, _plat, _link in rows])
    return [
        {
            "run_result_id": run.id,
            "platform_code": platform.code,
            "event_date": event.event_date,
            "event_number": event.event_number,
            "location_name": catalog_index.display_name(location, platform.code),
            "location_source_name": location.name,
            "location_city": location.city,
            "location_country": location.country,
            "position": run.position,
            "gender_position": run.gender_position,
            "finish_time_display": normalize_finish_time_display(
                run.finish_time_sec,
                run.finish_time_display,
            ),
            "finish_time_sec": run.finish_time_sec,
            "pace_display": run.pace_display,
            "pace_sec_per_km": run.pace_sec_per_km,
            "age_category": run.age_category,
            "is_pr": run_shows_personal_record(platform.code, run),
            "is_global_pr": run.is_global_pr,
            "is_location_pr": run.is_location_pr,
            "is_crosslinked": run.id in secondary_crosslinked_ids,
            "is_first_run": run.is_first_run,
            "is_first_run_at_location": run.is_first_run_at_location,
            "club_name": run.club_name,
            "achievement_labels": run.achievement_labels or [],
            "status": run.status,
            "is_test_event": event.is_test_event,
            "event_url": _activity_event_url(
                platform_code=platform.code,
                event=event,
                location=location,
                profile_url=platform_link.external_url,
                summary_source_url=summary_urls.get(
                    (event.platform_id, event.location_id, event.event_date)
                ),
            ),
            **_location_status_fields(catalog_index, location, platform.code),
        }
        for run, event, location, platform, platform_link in rows
    ]


def list_user_best_results(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]]:
    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    query = (
        db.query(RunResult, Event, Location, Platform, PlatformLink)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            RunResult.finish_time_sec.isnot(None),
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    # "Не в зачёте" duplicates (secondary crosslink) can't be the best result of a
    # system — the same protocol is counted on the primary platform.
    excluded_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    if excluded_ids:
        query = query.filter(RunResult.id.notin_(excluded_ids))

    rows = query.order_by(
        Platform.code.asc(),
        RunResult.finish_time_sec.asc(),
        Event.event_date.asc(),
    ).all()

    catalog_index = LocationCatalogIndex(db)
    summary_urls = _event_summary_source_urls(db, [event for _run, event, _loc, _plat, _link in rows])
    best_by_platform: dict[str, dict[str, object]] = {}
    platform_order = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}

    for run, event, location, platform, platform_link in rows:
        if platform.code in best_by_platform:
            continue
        best_by_platform[platform.code] = {
            "platform_code": platform.code,
            "event_date": event.event_date,
            "location_name": catalog_index.display_name(location, platform.code),
            "location_city": location.city,
            "finish_time_display": normalize_finish_time_display(
                run.finish_time_sec,
                run.finish_time_display,
            ),
            "finish_time_sec": run.finish_time_sec,
            "event_url": _activity_event_url(
                platform_code=platform.code,
                event=event,
                location=location,
                profile_url=platform_link.external_url,
                summary_source_url=summary_urls.get(
                    (event.platform_id, event.location_id, event.event_date)
                ),
            ),
        }

    return sorted(
        best_by_platform.values(),
        key=lambda item: (
            platform_order.get(str(item["platform_code"]), 99),
            str(item["platform_code"]),
        ),
    )


def list_user_personal_records(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]]:
    from app.services.personal_record_service import (
        run_is_personal_record_sql_filter,
        user_secondary_crosslinked_run_ids,
    )

    query = (
        db.query(RunResult, Event, Location, Platform, PlatformLink)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            run_is_personal_record_sql_filter(),
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    # "Не в зачёте" duplicates (secondary crosslink) never count as personal records.
    excluded_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    if excluded_ids:
        query = query.filter(RunResult.id.notin_(excluded_ids))

    rows = query.order_by(
        Event.event_date.desc(),
        Platform.code.asc(),
        RunResult.position.asc(),
    ).all()

    catalog_index = LocationCatalogIndex(db)
    summary_urls = _event_summary_source_urls(db, [event for _run, event, _loc, _plat, _link in rows])
    return [
        {
            "platform_code": platform.code,
            "event_date": event.event_date,
            "location_name": catalog_index.display_name(location, platform.code),
            "location_city": location.city,
            "finish_time_display": normalize_finish_time_display(
                run.finish_time_sec,
                run.finish_time_display,
            ),
            "finish_time_sec": run.finish_time_sec,
            "is_global_pr": run.is_global_pr,
            "event_url": _activity_event_url(
                platform_code=platform.code,
                event=event,
                location=location,
                profile_url=platform_link.external_url,
                summary_source_url=summary_urls.get(
                    (event.platform_id, event.location_id, event.event_date)
                ),
            ),
        }
        for run, event, location, platform, platform_link in rows
    ]


def list_user_volunteering(
    db: Session,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    include_test_events: bool = False,
) -> list[dict[str, object]]:
    query = (
        db.query(VolunteerResult, Event, Location, Platform, PlatformLink)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))
    # parkrun volunteer summaries are stored at 1970-01-01 by design — include them
    query = query.filter(
        (Platform.code == "parkrun") | (Event.event_date > date(1970, 1, 1))
    )
    rows = query.order_by(Event.event_date.desc()).offset(offset).limit(limit).all()

    event_ids = [event.id for _vol, event, _loc, _plat, _link in rows]
    crosslinked_event_ids: set[UUID] = set()
    if event_ids:
        cl_rows = (
            db.query(EventCrosslink.secondary_event_id)
            .filter(EventCrosslink.secondary_event_id.in_(event_ids))
            .all()
        )
        crosslinked_event_ids = {row[0] for row in cl_rows}

    catalog_index = LocationCatalogIndex(db)
    summary_urls = _event_summary_source_urls(db, [event for _vol, event, _loc, _plat, _link in rows])

    # Canonical volunteer_result_id для оценки одной физической площадки: min id
    # среди строк с одним (дата, каноническая локация) — так волонтёрство с
    # несколькими ролями (или кросслинк) оценивается один раз. Логика совпадает
    # с rating_service.list_eligible_runs, чтобы звезда была одинаковой на
    # дашборде и в таблице.
    canonical_by_group: dict[tuple[date, str], str] = {}
    for volunteer, event, location, platform, _link in rows:
        identity = catalog_index.canonical_identity_key(location, platform.code)
        key = (event.event_date, identity)
        vid = str(volunteer.id)
        if key not in canonical_by_group or vid < canonical_by_group[key]:
            canonical_by_group[key] = vid

    result: list[dict[str, object]] = []
    for volunteer, event, location, platform, platform_link in rows:
        identity = catalog_index.canonical_identity_key(location, platform.code)
        canonical_vol_id = canonical_by_group[(event.event_date, identity)]
        result.append(
            {
                "platform_code": platform.code,
                "event_date": event.event_date,
                "event_number": event.event_number,
                "location_name": catalog_index.display_name(location, platform.code),
                "location_source_name": location.name,
                "location_city": location.city,
                "location_country": location.country,
                "role": volunteer.role,
                "volunteer_result_id": volunteer.id,
                # Опаковый id для оценки (общий на все роли одного старта).
                "rating_entry_id": f"vol:{canonical_vol_id}",
                "is_crosslinked": event.id in crosslinked_event_ids,
                "is_test_event": event.is_test_event,
                "event_url": _activity_event_url(
                    platform_code=platform.code,
                    event=event,
                    location=location,
                    profile_url=platform_link.external_url,
                    summary_source_url=summary_urls.get(
                        (event.platform_id, event.location_id, event.event_date)
                    ),
                ),
                **_location_status_fields(catalog_index, location, platform.code),
            }
        )
    return result


def list_user_volunteer_role_stats(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]]:
    query = (
        db.query(Platform.code, VolunteerResult.role, func.count(VolunteerResult.id).label("count"))
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            VolunteerResult.role.isnot(None),
            VolunteerResult.role != "",
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    rows = query.group_by(Platform.code, VolunteerResult.role).all()
    platform_order = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}
    items = [
        {
            "platform_code": platform_code,
            "role": role.strip(),
            "count": int(count),
        }
        for platform_code, role, count in rows
        if role and role.strip()
    ]
    return sorted(
        items,
        key=lambda item: (
            -int(item["count"]),
            platform_order.get(str(item["platform_code"]), 99),
            str(item["role"]).casefold(),
        ),
    )


def get_sync_status_payload(db: Session, user_id: UUID) -> dict[str, object]:
    from app.services.sync_job_service import reconcile_user_sync_jobs

    reconcile_user_sync_jobs(db, user_id)

    links = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .order_by(PlatformLink.linked_at.desc())
        .all()
    )
    latest_job = get_latest_sync_job(db, user_id)
    cache = db.query(DashboardCache).filter(DashboardCache.user_id == user_id).one_or_none()

    platform_links = []
    for link, platform in links:
        error_message, error_details = present_sync_error(link.error_message)
        platform_links.append(
            {
                "platform_code": platform.code,
                "sync_status": link.sync_status.value,
                "last_user_sync_at": link.last_user_sync_at,
                "error_message": error_message,
                "error_details": error_details,
            }
        )

    latest_job_payload = None
    if latest_job:
        job_error_message, job_error_details = present_sync_error(latest_job.error_message)
        latest_job_payload = {
            "id": latest_job.id,
            "status": latest_job.status.value,
            "trigger": latest_job.trigger.value,
            "started_at": latest_job.started_at,
            "finished_at": latest_job.finished_at,
            "error_message": job_error_message,
            "error_details": job_error_details,
            "created_at": latest_job.created_at,
        }

    return {
        "platform_links": platform_links,
        "latest_job": latest_job_payload,
        "dashboard_cache_computed_at": cache.computed_at if cache else None,
    }


def get_dashboard_payload(db: Session, user: User) -> dict[str, object]:
    cache = db.query(DashboardCache).filter(DashboardCache.user_id == user.id).one_or_none()
    if cache is None or (cache.stats or {}).get("analytics", {}).get("analytics_version") != ANALYTICS_VERSION:
        cache = recompute_dashboard_cache(db, user.id)
        db.commit()
        db.refresh(cache)

    links = list_user_profile_links_summary(db, user.id)
    return {
        "stats": cache.stats,
        "computed_at": cache.computed_at,
        "platform_links": links,
    }


def list_user_profile_links_summary(db: Session, user_id: UUID) -> list[dict[str, object]]:
    rows = (
        db.query(PlatformLink, Platform)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .order_by(PlatformLink.linked_at.desc())
        .all()
    )
    return [
        {
            "platform_code": platform.code,
            "external_user_id": link.external_user_id,
            "sync_status": link.sync_status.value,
            "last_user_sync_at": link.last_user_sync_at,
        }
        for link, platform in rows
    ]


def create_sync_job(
    db: Session,
    user_id: UUID,
    trigger: SyncJobTrigger,
    *,
    platform_link_id: UUID | None = None,
) -> SyncJob:
    job = SyncJob(
        user_id=user_id,
        platform_link_id=platform_link_id,
        trigger=trigger,
        status=SyncJobStatus.queued,
    )
    db.add(job)
    db.flush()
    return job
