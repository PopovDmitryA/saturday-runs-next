from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Date, cast, distinct, func
from sqlalchemy.orm import Session

from app.core.site_stats import read_pageviews_by_day
from app.models import (
    AuthLoginRequest,
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    SyncJob,
    SyncJobStatus,
    User,
)

PLATFORM_CODES = ("five_verst", "s95", "parkrun")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _since(days: int) -> datetime:
    return _utcnow() - timedelta(days=days)


def get_admin_site_stats(db: Session, *, period_days: int = 30) -> dict[str, object]:
    if period_days < 1 or period_days > 365:
        period_days = 30
    since = _since(period_days)

    users_total = db.query(func.count(User.id)).scalar() or 0
    users_with_consent = db.query(func.count(User.id)).filter(User.consent_accepted.is_(True)).scalar() or 0
    users_active_period = (
        db.query(func.count(User.id))
        .filter(User.last_login_at.isnot(None), User.last_login_at >= since)
        .scalar()
        or 0
    )

    platform_links_total = db.query(func.count(PlatformLink.id)).scalar() or 0
    users_with_any_link = db.query(func.count(distinct(PlatformLink.user_id))).scalar() or 0

    links_by_platform_rows = (
        db.query(Platform.code, func.count(PlatformLink.id))
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .group_by(Platform.code)
        .all()
    )
    links_by_platform = {code: 0 for code in PLATFORM_CODES}
    for code, count in links_by_platform_rows:
        links_by_platform[code] = int(count)

    users_with_all_three = (
        db.query(func.count())
        .select_from(
            db.query(PlatformLink.user_id)
            .join(Platform, PlatformLink.platform_id == Platform.id)
            .group_by(PlatformLink.user_id)
            .having(func.count(distinct(Platform.code)) >= len(PLATFORM_CODES))
            .subquery()
        )
        .scalar()
        or 0
    )

    pageviews_by_day = read_pageviews_by_day(days=period_days)

    overview = {
        "users_total": users_total,
        "users_with_consent": users_with_consent,
        "users_active_period": users_active_period,
        "users_with_any_link": users_with_any_link,
        "users_with_all_three_links": users_with_all_three,
        "platform_links_total": platform_links_total,
        "links_by_platform": links_by_platform,
        "participants_total": db.query(func.count(Participant.id)).scalar() or 0,
        "events_total": db.query(func.count(Event.id)).scalar() or 0,
        "run_results_total": db.query(func.count(RunResult.id)).scalar() or 0,
        "locations_total": db.query(func.count(Location.id)).scalar() or 0,
        "sync_jobs_total": db.query(func.count(SyncJob.id)).scalar() or 0,
        "sync_jobs_active": db.query(func.count(SyncJob.id))
        .filter(SyncJob.status.in_([SyncJobStatus.queued, SyncJobStatus.running]))
        .scalar()
        or 0,
        "login_requests_period": db.query(func.count(AuthLoginRequest.id))
        .filter(AuthLoginRequest.created_at >= since)
        .scalar()
        or 0,
        "pageviews_period": sum(int(row.get("total", 0)) for row in pageviews_by_day),
        "unique_visitors_period": sum(int(row.get("unique_visitors", 0)) for row in pageviews_by_day),
    }

    users_new_by_day = _series_from_db(
        db,
        User.created_at,
        since=since,
        period_days=period_days,
    )
    links_new_by_day = _series_from_db(
        db,
        PlatformLink.linked_at,
        since=since,
        period_days=period_days,
    )
    logins_by_day = _series_from_db(
        db,
        User.last_login_at,
        since=since,
        period_days=period_days,
        not_null_column=User.last_login_at,
    )
    login_requests_by_day = _series_from_db(
        db,
        AuthLoginRequest.created_at,
        since=since,
        period_days=period_days,
    )

    overview["users_new_period"] = sum(int(row["value"]) for row in users_new_by_day)
    overview["links_new_period"] = sum(int(row["value"]) for row in links_new_by_day)
    overview["logins_period"] = sum(int(row["value"]) for row in logins_by_day)

    return {
        "period_days": period_days,
        "generated_at": _utcnow(),
        "overview": overview,
        "users_new_by_day": users_new_by_day,
        "links_new_by_day": links_new_by_day,
        "logins_by_day": logins_by_day,
        "login_requests_by_day": login_requests_by_day,
        "pageviews_by_day": pageviews_by_day,
    }


def _series_from_db(
    db: Session,
    column,
    *,
    since: datetime,
    period_days: int,
    not_null_column=None,
) -> list[dict[str, object]]:
    day_expr = cast(func.date_trunc("day", column), Date)
    query = db.query(day_expr.label("date"), func.count().label("value")).filter(column >= since)
    if not_null_column is not None:
        query = query.filter(not_null_column.isnot(None))
    rows = query.group_by(day_expr).order_by(day_expr).all()

    by_date = {row.date.isoformat(): int(row.value) for row in rows if row.date is not None}
    today = _utcnow().date()
    series: list[dict[str, object]] = []
    for offset in range(period_days - 1, -1, -1):
        day = today - timedelta(days=offset)
        key = day.isoformat()
        series.append({"date": key, "value": by_date.get(key, 0)})
    return series
