"""Админка «Планы»: сводка, динамика и лента планировщика забегов (§10c).

Строки run_plans и run_plan_series здесь читаются как записи: когда и как
люди их ставили — лента, «за сколько дней до старта», публично или приватно,
новые люди по дням. Кто планирует на дату — нагрузка по стартам и план → факт
— только через planned_starts: вхождения постоянных планов считаются при
чтении, и голый run_plans их бы потерял.

Админ видит все планы, включая приватные: приватность — про чужие глаза на
сайте, а не про админку. В ленте у приватных пометка.

Запросов — константа: сводка, динамика, локации и уведомления — по одному
агрегату, нагрузка и факты — planned_starts на всех планирующих разом и три
запроса протоколов.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, text, tuple_
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Participant,
    PlatformLink,
    RunPlanSeries,
    RunResult,
    VolunteerResult,
)
from app.services.planned_starts_service import PlannedStart, planned_starts, sleep_states
from app.services.run_plan_service import _Identities, canonical_identity_keys
from app.services.start_calendar_service import (
    _next_saturday,
    identity_rows,
    moscow_today,
    normalize_identity_key,
    special_dates,
)

PLAN_DELIVERY_KINDS = ("plan_reminders", "plan_cancellations", "plan_series")
# Нагрузка по стартам — столько недель вперёд.
LOAD_WEEKS = 8
TOP_LOCATIONS = 10
_TZ = "Europe/Moscow"

# За сколько дней до старта ставят разовый план: (от, до включительно, подпись).
LEAD_BUCKETS: tuple[tuple[int, int | None, str], ...] = (
    (0, 0, "в день старта"),
    (1, 1, "накануне"),
    (2, 3, "за 2–3 дня"),
    (4, 7, "за 4–7 дней"),
    (8, 14, "за 1–2 недели"),
    (15, 30, "за 2–4 недели"),
    (31, None, "больше месяца"),
)

# Все записи планировщика одной выборкой: разовые планы, отметки «Не бегаю» и
# постоянные планы (их создание, остановка и пауза — отдельными событиями).
_FEED_SQL = """
    SELECT 'plan' AS kind, p.id, p.user_id, p.created_at AS at, p.plan_date AS on_date, NULL::date AS until,
           p.location_key, p.location_id, p.is_public, NULL::timestamptz AS stopped_at, NULL::date AS sleeping_since
      FROM run_plans p WHERE p.location_key IS NOT NULL
    UNION ALL
    SELECT 'rest', p.id, p.user_id, p.created_at, p.plan_date, NULL, NULL, NULL, p.is_public, NULL, NULL
      FROM run_plans p WHERE p.location_key IS NULL
    UNION ALL
    SELECT 'series', s.id, s.user_id, s.created_at, s.starts_on, s.until, s.location_key, s.location_id,
           s.is_public, s.stopped_at, s.sleeping_since
      FROM run_plan_series s
    UNION ALL
    SELECT 'series_stop', s.id, s.user_id, s.stopped_at, s.starts_on, s.until, s.location_key, s.location_id,
           s.is_public, s.stopped_at, s.sleeping_since
      FROM run_plan_series s WHERE s.stopped_at IS NOT NULL
    UNION ALL
    SELECT 'series_sleep', s.id, s.user_id, COALESCE(s.sleep_notified_at, s.sleeping_since::timestamptz),
           s.sleeping_since, s.until, s.location_key, s.location_id, s.is_public, s.stopped_at, s.sleeping_since
      FROM run_plan_series s WHERE s.sleeping_since IS NOT NULL
"""


def _user_label(serial_id: int | None, username: str | None, display_name: str | None) -> str:
    if username:
        return f"@{username}"
    return (display_name or "").strip() or f"№{serial_id}"


def _series_status(stopped_at: datetime | None, until: date | None, sleeping_since: date | None, today: date) -> str:
    if stopped_at is not None or (until is not None and until < today):
        return "stopped"
    return "sleeping" if sleeping_since is not None else "active"


# ---------------------------------------------------------------- сводка


def _summary_counts(db: Session, today: date) -> dict[str, int]:
    row = db.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE location_key IS NOT NULL AND plan_date >= :today) AS plans_future,
              count(*) FILTER (WHERE location_key IS NOT NULL AND plan_date < :today) AS plans_past,
              count(*) FILTER (WHERE location_key IS NOT NULL AND is_public) AS plans_public,
              count(*) FILTER (WHERE location_key IS NULL) AS rest_total,
              count(*) FILTER (WHERE location_key IS NULL AND plan_date >= :today) AS rest_future,
              count(DISTINCT user_id) FILTER (WHERE location_key IS NULL) AS rest_people,
              (SELECT count(*) FROM run_plan_series) AS series_total,
              (SELECT count(*) FROM run_plan_series
                WHERE stopped_at IS NOT NULL OR (until IS NOT NULL AND until < :today)) AS series_stopped,
              (SELECT count(*) FROM run_plan_series WHERE is_public) AS series_public,
              (SELECT count(*) FROM (
                 SELECT user_id FROM run_plans WHERE location_key IS NOT NULL
                 UNION SELECT user_id FROM run_plan_series) people) AS people_total
            FROM run_plans
            """
        ),
        {"today": today},
    ).one()
    return {key: int(value or 0) for key, value in row._mapping.items()}


def _future_one_off_users(db: Session, today: date) -> set[UUID]:
    return {
        user_id
        for (user_id,) in db.execute(
            text("SELECT DISTINCT user_id FROM run_plans WHERE location_key IS NOT NULL AND plan_date >= :today"),
            {"today": today},
        )
    }


def _past_users(db: Session, today: date) -> tuple[set[UUID], date | None]:
    """Люди с планами в прошлом (разовыми или вхождениями постоянного) и самая ранняя дата."""
    rows = db.execute(
        text(
            """
            SELECT user_id, min(on_date) FROM (
              SELECT user_id, plan_date AS on_date FROM run_plans
               WHERE location_key IS NOT NULL AND plan_date < :today
              UNION ALL
              SELECT user_id, starts_on FROM run_plan_series WHERE starts_on < :today
            ) past GROUP BY user_id
            """
        ),
        {"today": today},
    ).all()
    earliest = min((on_date for _user_id, on_date in rows), default=None)
    return {user_id for user_id, _on_date in rows}, earliest


def _live_series(db: Session, today: date, identities: _Identities) -> tuple[list[RunPlanSeries], set[UUID]]:
    """(действующие постоянные, id тех, что сейчас на паузе). Пауза — тем же
    правилом, что у человека: результат на точке после начала сна её снимает."""
    series_list = (
        db.query(RunPlanSeries)
        .filter(RunPlanSeries.stopped_at.is_(None), or_(RunPlanSeries.until.is_(None), RunPlanSeries.until >= today))
        .all()
    )
    location_ids_of: dict[UUID, Collection[UUID]] = {}
    for series in series_list:
        if series.sleeping_since is None:
            continue
        identity = identities.get(series.location_key)
        if identity is not None:
            location_ids_of[series.id] = identity.location_ids
        elif series.location_id is not None:
            location_ids_of[series.id] = [series.location_id]
    states = sleep_states(db, series_list, location_ids_of)
    return series_list, {series.id for series in series_list if states[series.id].sleeping}


def _facts(db: Session, entries: list[PlannedStart], identities: _Identities) -> dict[str, int]:
    """План → факт по прошедшим планам, правилами plan_facts: пробежал важнее
    волонтёрства, волонтёрство — тоже «был на старте»; событие есть, а человека
    в протоколе нет — «не пришёл»; события нет (протокол не загружен) — «нет
    данных». Без поправок на зеркала кросслинков — для сводки этого хватает."""
    counts = {"total": len(entries), "ran": 0, "volunteered": 0, "missed": 0, "unknown": 0}
    if not entries:
        return counts
    ids_by_entry: list[set[UUID]] = []
    pairs: set[tuple[UUID, date]] = set()
    for entry in entries:
        location_ids: set[UUID] = set()
        identity = identities.get(entry.key)
        if identity is not None:
            location_ids.update(identity.location_ids)
        if entry.location_id is not None:
            location_ids.add(entry.location_id)
        ids_by_entry.append(location_ids)
        pairs.update((location_id, entry.plan_date) for location_id in location_ids)
    events: dict[tuple[UUID, date], list[UUID]] = defaultdict(list)
    for event_id, location_id, event_date in (
        db.query(Event.id, Event.location_id, Event.event_date)
        .filter(tuple_(Event.location_id, Event.event_date).in_(list(pairs)), Event.is_test_event.is_(False))
        .all()
    ):
        events[(location_id, event_date)].append(event_id)
    event_ids = [event_id for items in events.values() for event_id in items]
    users = list({entry.user_id for entry in entries})
    ran: set[tuple[UUID, UUID]] = set()
    volunteered: set[tuple[UUID, UUID]] = set()
    if event_ids:
        link_join = (PlatformLink.platform_id == Participant.platform_id) & (
            PlatformLink.external_user_id == Participant.external_user_id
        )
        for model, target in ((RunResult, ran), (VolunteerResult, volunteered)):
            target.update(
                db.query(PlatformLink.user_id, model.event_id)
                .join(Participant, model.participant_id == Participant.id)
                .join(PlatformLink, link_join)
                .filter(PlatformLink.user_id.in_(users), model.event_id.in_(event_ids))
                .all()
            )
    for entry, location_ids in zip(entries, ids_by_entry, strict=True):
        day = [event_id for location_id in location_ids for event_id in events.get((location_id, entry.plan_date), [])]
        if any((entry.user_id, event_id) in ran for event_id in day):
            counts["ran"] += 1
        elif any((entry.user_id, event_id) in volunteered for event_id in day):
            counts["volunteered"] += 1
        elif day:
            counts["missed"] += 1
        else:
            counts["unknown"] += 1
    return counts


def _start_dates(db: Session, date_from: date, date_to: date) -> dict[date, str | None]:
    """Даты нагрузки: субботы окна и особые даты (название — у особых)."""
    result: dict[date, str | None] = {}
    on_date = _next_saturday(date_from)
    while on_date <= date_to:
        result[on_date] = None
        on_date += timedelta(days=7)
    for special in special_dates(db, date_from, date_to):
        result[special.start_date] = special.title
    return dict(sorted(result.items()))


def _load(entries: list[PlannedStart], dates: dict[date, str | None]) -> list[dict[str, Any]]:
    by_date: dict[date, list[PlannedStart]] = defaultdict(list)
    for entry in entries:
        by_date[entry.plan_date].append(entry)
    return [
        {
            "date": on_date,
            "special_title": title,
            "people": len({entry.user_id for entry in by_date.get(on_date, [])}),
            "one_off": sum(1 for entry in by_date.get(on_date, []) if not entry.regular),
            "regular": sum(1 for entry in by_date.get(on_date, []) if entry.regular),
            "locations": len({entry.key for entry in by_date.get(on_date, [])}),
        }
        for on_date, title in dates.items()
    ]


def _daily(db: Session, today: date, period_days: int) -> tuple[list[dict[str, Any]], int]:
    """Новые планы и новые люди по дням создания (МСК) и сколько людей было до окна."""
    since_day = today - timedelta(days=period_days - 1)
    rows = db.execute(
        text(
            """
            WITH records AS (
              SELECT user_id, created_at, 'plans' AS tag FROM run_plans WHERE location_key IS NOT NULL
              UNION ALL SELECT user_id, created_at, 'series' FROM run_plan_series
            ), firsts AS (SELECT user_id, min(created_at) AS first_at FROM records GROUP BY user_id)
            SELECT tag, (created_at AT TIME ZONE :tz)::date AS day, count(*) FROM records
             WHERE (created_at AT TIME ZONE :tz)::date >= :since GROUP BY 1, 2
            UNION ALL
            SELECT 'people', (first_at AT TIME ZONE :tz)::date, count(*) FROM firsts
             WHERE (first_at AT TIME ZONE :tz)::date >= :since GROUP BY 1, 2
            UNION ALL
            SELECT 'before', NULL, count(*) FROM firsts WHERE (first_at AT TIME ZONE :tz)::date < :since
            """
        ),
        {"tz": _TZ, "since": since_day},
    ).all()
    values: dict[tuple[str, date], int] = {}
    people_before = 0
    for tag, day, count in rows:
        if tag == "before":
            people_before = int(count)
        else:
            values[(tag, day)] = int(count)
    days: list[dict[str, Any]] = []
    running = people_before
    for offset in range(period_days):
        day = since_day + timedelta(days=offset)
        running += values.get(("people", day), 0)
        days.append(
            {
                "date": day,
                "plans": values.get(("plans", day), 0),
                "series": values.get(("series", day), 0),
                "people": values.get(("people", day), 0),
                "people_total": running,
            }
        )
    return days, people_before


def _lead_times(db: Session) -> tuple[list[dict[str, Any]], float | None]:
    rows = db.execute(
        text(
            """
            SELECT GREATEST(plan_date - (created_at AT TIME ZONE :tz)::date, 0) AS lead, count(*)
              FROM run_plans WHERE location_key IS NOT NULL GROUP BY 1 ORDER BY 1
            """
        ),
        {"tz": _TZ},
    ).all()
    buckets = [{"label": label, "count": 0} for _lo, _hi, label in LEAD_BUCKETS]
    expanded: list[int] = []
    for lead, count in rows:
        for index, (lo, hi, _label) in enumerate(LEAD_BUCKETS):
            if lead >= lo and (hi is None or lead <= hi):
                buckets[index]["count"] += int(count)
                break
        expanded.extend([int(lead)] * int(count))
    median = None
    if expanded:
        middle = len(expanded) // 2
        median = float(expanded[middle]) if len(expanded) % 2 else (expanded[middle - 1] + expanded[middle]) / 2
    return buckets, median


def _people_mix(db: Session) -> dict[str, int]:
    row = db.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE one_off AND NOT regular) AS only_one_off,
                   count(*) FILTER (WHERE regular AND NOT one_off) AS only_regular,
                   count(*) FILTER (WHERE one_off AND regular) AS both
              FROM (
                SELECT user_id, bool_or(src = 'p') AS one_off, bool_or(src = 's') AS regular FROM (
                  SELECT user_id, 'p' AS src FROM run_plans WHERE location_key IS NOT NULL
                  UNION ALL SELECT user_id, 's' FROM run_plan_series
                ) records GROUP BY user_id
              ) people
            """
        )
    ).one()
    return {key: int(value or 0) for key, value in row._mapping.items()}


def _locations(
    db: Session,
    today: date,
    identities: _Identities,
    live_series: list[RunPlanSeries],
    sleeping: set[UUID],
    load_entries: list[PlannedStart],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(топ локаций, все локации с планами для фильтра, «скоро открытие» с планами)."""
    records = db.execute(
        text(
            """
            SELECT location_key, location_id, user_id, plan_date >= :today AS future, false AS regular
              FROM run_plans WHERE location_key IS NOT NULL
            UNION ALL
            SELECT location_key, location_id, user_id, false, true FROM run_plan_series
            """
        ),
        {"today": today},
    ).all()
    canonical = canonical_identity_keys(db, [row.location_id for row in records if row.location_id is not None])
    location_rows = {
        location.id: location
        for location in db.query(Location).filter(
            Location.id.in_({row.location_id for row in records if row.location_id is not None})
        )
    }

    def key_of(location_key: str, location_id: UUID | None) -> str:
        found = canonical.get(location_id) if location_id is not None else None
        return found or normalize_identity_key(location_key) or location_key

    live_keys = {key_of(series.location_key, series.location_id) for series in live_series if series.id not in sleeping}
    groups: dict[str, dict[str, Any]] = {}
    for row in records:
        key = key_of(row.location_key, row.location_id)
        group = groups.setdefault(
            key, {"key": key, "one_off": 0, "regular": 0, "people": set(), "future": False, "refs": set()}
        )
        group["regular" if row.regular else "one_off"] += 1
        group["people"].add(row.user_id)
        group["future"] = group["future"] or bool(row.future)
        if row.location_id is not None:
            group["refs"].add(row.location_id)
    for key in live_keys:
        if key in groups:
            groups[key]["future"] = True
    upcoming_load: dict[str, int] = defaultdict(int)
    for entry in load_entries:
        upcoming_load[entry.key] += 1

    def cheap(group: dict[str, Any]) -> dict[str, Any]:
        refs = [location_rows[location_id] for location_id in group["refs"] if location_id in location_rows]
        refs.sort(key=lambda location: location.external_key)
        return {
            "key": group["key"],
            "name": refs[0].name if refs else group["key"],
            "slug": refs[0].external_key.strip().lower() if refs else None,
            "upcoming": any(location.is_upcoming for location in refs),
        }

    ordered = sorted(groups.values(), key=lambda group: (-(group["one_off"] + group["regular"]), group["key"]))
    top: list[dict[str, Any]] = []
    for group in ordered[:TOP_LOCATIONS]:
        item = cheap(group)
        identity = identities.get(group["key"])
        if identity is not None:
            item.update(name=identity.name or item["name"], slug=identity.slug or item["slug"], upcoming=identity.upcoming)
        top.append(
            {
                **item,
                "plans": group["one_off"],
                "series": group["regular"],
                "people": len(group["people"]),
                "next_weeks": upcoming_load.get(group["key"], 0),
            }
        )
    options = [{**cheap(group), "count": group["one_off"] + group["regular"]} for group in ordered]
    upcoming = [
        {**cheap(group), "count": group["one_off"] + group["regular"]}
        for group in ordered
        if group["future"] and cheap(group)["upcoming"]
    ]
    return top, options, upcoming


def _deliveries(db: Session, since: datetime) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT kind, status, count(*) AS total, count(*) FILTER (WHERE created_at >= :since) AS period
              FROM notification_deliveries WHERE kind = ANY(:kinds) GROUP BY kind, status
            """
        ),
        {"since": since, "kinds": list(PLAN_DELIVERY_KINDS)},
    ).all()
    result = {kind: {"kind": kind, "total": 0, "period": 0, "by_status": {}} for kind in PLAN_DELIVERY_KINDS}
    for kind, status, total, period in rows:
        item = result[kind]
        item["total"] += int(total)
        item["period"] += int(period)
        item["by_status"][status] = int(total)
    return list(result.values())


def get_admin_plans_overview(db: Session, *, period_days: int, today: date | None = None) -> dict[str, Any]:
    today = today or moscow_today()
    identities = _Identities(db, today)
    counts = _summary_counts(db, today)
    live_series, sleeping = _live_series(db, today, identities)

    future_users = _future_one_off_users(db, today)
    candidates = future_users | {series.user_id for series in live_series}
    load_to = today + timedelta(weeks=LOAD_WEEKS) - timedelta(days=1)
    load_entries = (
        planned_starts(db, today=today, date_from=today, date_to=load_to, user_ids=candidates, identities=identities)
        if candidates
        else []
    )

    past_users, earliest = _past_users(db, today)
    past_entries = (
        planned_starts(
            db,
            today=today,
            date_from=earliest,
            date_to=today - timedelta(days=1),
            user_ids=past_users,
            identities=identities,
        )
        if past_users and earliest is not None
        else []
    )

    daily, _people_before = _daily(db, today, period_days)
    lead, lead_median = _lead_times(db)
    top, options, upcoming = _locations(db, today, identities, live_series, sleeping, load_entries)
    one_off_total = counts["plans_future"] + counts["plans_past"]
    return {
        "generated_at": datetime.now(UTC),
        "today": today,
        "period_days": period_days,
        "summary": {
            "people_total": counts["people_total"],
            "people_future": len(future_users | {s.user_id for s in live_series if s.id not in sleeping}),
            "plans_future": counts["plans_future"],
            "plans_past": counts["plans_past"],
            "plans_public": counts["plans_public"],
            "series_active": len(live_series) - len(sleeping),
            "series_sleeping": len(sleeping),
            "series_stopped": counts["series_stopped"],
            "series_public": counts["series_public"],
            "series_total": counts["series_total"],
            "rest_total": counts["rest_total"],
            "rest_future": counts["rest_future"],
            "rest_people": counts["rest_people"],
        },
        "facts": _facts(db, past_entries, identities),
        "daily": daily,
        "load": _load(load_entries, _start_dates(db, today, load_to)),
        "lead_times": lead,
        "lead_median_days": lead_median,
        "mix": {
            **_people_mix(db),
            "one_off_plans": one_off_total,
            "series": counts["series_total"],
            "public_plans": counts["plans_public"],
            "private_plans": one_off_total - counts["plans_public"],
            "public_series": counts["series_public"],
            "private_series": counts["series_total"] - counts["series_public"],
        },
        "top_locations": top,
        "locations": options,
        "upcoming_locations": upcoming,
        "deliveries": _deliveries(db, datetime.now(UTC) - timedelta(days=period_days)),
    }


# ---------------------------------------------------------------- лента


def get_admin_plans_feed(
    db: Session,
    *,
    future_only: bool = False,
    location_key: str | None = None,
    person: str | None = None,
    limit: int = 50,
    offset: int = 0,
    today: date | None = None,
) -> dict[str, Any]:
    """Все записи планировщика по убыванию времени: кто, когда, на какую дату и
    куда, разовый или постоянный, публично или приватно, «Не бегаю»."""
    today = today or moscow_today()
    where: list[str] = []
    params: dict[str, Any] = {"today": today, "limit": limit, "offset": offset}
    if future_only:
        where.append(
            "((f.kind IN ('plan', 'rest') AND f.on_date >= :today) OR (f.kind = 'series' AND f.stopped_at IS NULL"
            " AND (f.until IS NULL OR f.until >= :today)))"
        )
    if location_key:
        key = normalize_identity_key(location_key) or location_key
        ids = [location.id for location, _code in identity_rows(db, key)]
        params["key"] = key
        params["raw_key"] = location_key
        condition = "f.location_key IN (:key, :raw_key)"
        if ids:
            params["ids"] = ids
            condition = f"({condition} OR f.location_id = ANY(:ids))"
        where.append(condition)
    person = (person or "").strip().lstrip("@")
    if person:
        if person.isdigit():
            where.append("u.serial_id = :serial")
            params["serial"] = int(person)
        else:
            where.append(
                "(u.display_name ILIKE :like OR u.telegram_username ILIKE :like OR u.public_slug ILIKE :like)"
            )
            params["like"] = f"%{person}%"
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    base = f"FROM ({_FEED_SQL}) f JOIN users u ON u.id = f.user_id LEFT JOIN locations l ON l.id = f.location_id {clause}"
    total = int(db.execute(text(f"SELECT count(*) {base}"), params).scalar() or 0)
    rows = db.execute(
        text(
            f"""
            SELECT f.*, u.serial_id, u.telegram_username, u.display_name, u.profile_private,
                   l.name AS location_name, l.external_key AS location_slug
              {base}
             ORDER BY f.at DESC NULLS LAST, f.id
             LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).all()
    return {
        "items": [
            {
                "id": f"{row.kind}:{row.id}",
                "kind": row.kind,
                "at": row.at,
                "user_serial_id": row.serial_id,
                "user_label": _user_label(row.serial_id, row.telegram_username, row.display_name),
                "profile_private": bool(row.profile_private),
                "on_date": row.on_date,
                "until": row.until,
                "location_name": row.location_name,
                "location_slug": row.location_slug.strip().lower() if row.location_slug else None,
                "is_public": bool(row.is_public),
                "series_status": (
                    _series_status(row.stopped_at, row.until, row.sleeping_since, today)
                    if row.kind.startswith("series")
                    else None
                ),
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
