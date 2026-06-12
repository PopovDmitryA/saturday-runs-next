from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.migration.legacy_db import legacy_connection, legacy_rows, legacy_scalar
from app.services.parkrun_legacy_seed import legacy_database_url as resolve_legacy_url

RATING_CACHE_TTL_SECONDS = 3600
RATING_CACHE_KEY_PREFIX = "ratings:five_verst:run_count:v1"

RATING_TITLE = "Рейтинг количества пробежек"
RATING_DESCRIPTION = (
    "ТОП-1000 участников забегов 5 вёрст по количеству пробежек. "
    "Столбец «+1 на последней неделе» — участие в забеге в дату последней пробежки. "
    "«Кол-во пропустивших» — сколько участников выше или на той же позиции не бегали в эту дату."
)

LEGACY_RATING_SQL = """
WITH RankedEvents AS (
    SELECT
        dp.user_id,
        dp.name_runner,
        COUNT(dp.name_point) AS sum_event,
        RANK() OVER (ORDER BY COUNT(dp.name_point) DESC) AS rank
    FROM details_protocol dp
    INNER JOIN list_all_events le ON dp.name_point = le.name_point AND dp.date_event = le.date_event
    WHERE dp.status_runner = 'active_runner' AND le.is_test = false
    GROUP BY dp.user_id, dp.name_runner
),
MaxDate AS (
    SELECT MAX(date_event) AS max_date_event
    FROM details_protocol
),
LastActivity AS (
    SELECT DISTINCT ON (dp.user_id)
        dp.user_id,
        dp.name_point
    FROM details_protocol dp
    INNER JOIN list_all_events le ON dp.name_point = le.name_point AND dp.date_event = le.date_event
    WHERE dp.date_event = (SELECT max_date_event FROM MaxDate)
      AND dp.status_runner = 'active_runner'
      AND le.is_test = false
    ORDER BY dp.user_id
),
RankedWithDelta AS (
    SELECT
        re.rank,
        re.name_runner,
        re.sum_event,
        CASE WHEN la.user_id IS NOT NULL THEN TRUE ELSE FALSE END AS delta_last_week,
        la.name_point AS last_location
    FROM RankedEvents re
    LEFT JOIN LastActivity la ON re.user_id = la.user_id
)
SELECT
    rw.rank,
    rw.name_runner,
    rw.sum_event,
    rw.delta_last_week,
    rw.last_location,
    SUM(CASE WHEN rw.delta_last_week = FALSE THEN 1 ELSE 0 END)
        OVER (ORDER BY rw.rank RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS count_pass
FROM RankedWithDelta rw
ORDER BY rw.sum_event DESC
LIMIT :limit
"""

LK_RATING_SQL = """
WITH five_verst AS (
    SELECT id AS platform_id
    FROM platforms
    WHERE code = 'five_verst'
    LIMIT 1
),
eligible_runs AS (
    SELECT
        rr.participant_id,
        COALESCE(NULLIF(TRIM(pt.display_name), ''), pt.external_user_id) AS runner_name,
        e.event_date,
        l.name AS location_name
    FROM run_results rr
    JOIN events e ON e.id = rr.event_id
    JOIN participants pt ON pt.id = rr.participant_id
    JOIN locations l ON l.id = e.location_id
    JOIN five_verst fv ON fv.platform_id = e.platform_id
    WHERE e.is_test_event = false
      AND rr.participant_id IS NOT NULL
      AND COALESCE(rr.status, 'active_runner') = 'active_runner'
),
RankedEvents AS (
    SELECT
        participant_id,
        runner_name,
        COUNT(*) AS sum_event,
        RANK() OVER (ORDER BY COUNT(*) DESC) AS rank
    FROM eligible_runs
    GROUP BY participant_id, runner_name
),
MaxDate AS (
    SELECT MAX(e.event_date) AS max_date_event
    FROM events e
    JOIN five_verst fv ON fv.platform_id = e.platform_id
),
LastActivity AS (
    SELECT DISTINCT ON (er.participant_id)
        er.participant_id,
        er.location_name
    FROM eligible_runs er
    CROSS JOIN MaxDate md
    WHERE er.event_date = md.max_date_event
    ORDER BY er.participant_id
),
RankedWithDelta AS (
    SELECT
        re.rank,
        re.runner_name,
        re.sum_event,
        CASE WHEN la.participant_id IS NOT NULL THEN TRUE ELSE FALSE END AS delta_last_week,
        la.location_name AS last_location
    FROM RankedEvents re
    LEFT JOIN LastActivity la ON re.participant_id = la.participant_id
)
SELECT
    rw.rank,
    rw.runner_name,
    rw.sum_event,
    rw.delta_last_week,
    rw.last_location,
    SUM(CASE WHEN rw.delta_last_week = FALSE THEN 1 ELSE 0 END)
        OVER (ORDER BY rw.rank RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS count_pass
FROM RankedWithDelta rw
ORDER BY rw.sum_event DESC
LIMIT :limit
"""


def _normalize_rows(raw_rows: list[Any]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for row in raw_rows:
        mapping = row._mapping if hasattr(row, "_mapping") else row
        items.append(
            {
                "rank": int(mapping["rank"]),
                "runner_name": str(mapping.get("name_runner") or mapping.get("runner_name") or "").strip(),
                "run_count": int(mapping["sum_event"]),
                "ran_on_latest_date": bool(mapping["delta_last_week"]),
                "latest_location": (
                    str(mapping["last_location"]).strip() if mapping.get("last_location") else None
                ),
                "skipped_above_count": int(mapping["count_pass"]),
            }
        )
    return items


def _lk_latest_event_date(db: Session) -> date | None:
    value = db.execute(
        text(
            """
            SELECT MAX(e.event_date)
            FROM events e
            JOIN platforms p ON p.id = e.platform_id
            WHERE p.code = 'five_verst'
            """
        )
    ).scalar()
    return value if isinstance(value, date) else None


def _lk_data_updated_at(db: Session) -> datetime | None:
    value = db.execute(
        text(
            """
            SELECT MAX(GREATEST(rr.updated_at, e.updated_at))
            FROM run_results rr
            JOIN events e ON e.id = rr.event_id
            JOIN platforms p ON p.id = e.platform_id
            WHERE p.code = 'five_verst'
            """
        )
    ).scalar()
    return value if isinstance(value, datetime) else None


def _legacy_latest_event_date(conn: Any) -> date | None:
    value = legacy_scalar(conn, "SELECT MAX(date_event) FROM details_protocol")
    return value if isinstance(value, date) else None


def _legacy_data_updated_at(conn: Any) -> datetime | None:
    value = legacy_scalar(
        conn,
        """
        SELECT MAX(update_date)
        FROM update_table
        WHERE table_name = 'details_protocol'
        """,
    )
    return value if isinstance(value, datetime) else None


def _load_from_lk(db: Session, *, limit: int) -> list[dict[str, object]]:
    rows = db.execute(text(LK_RATING_SQL), {"limit": limit}).all()
    return _normalize_rows(rows)


def _load_from_legacy(conn: Any, *, limit: int) -> list[dict[str, object]]:
    rows = legacy_rows(conn, LEGACY_RATING_SQL, {"limit": limit})
    return _normalize_rows(rows)


def _lk_has_data(db: Session) -> bool:
    count = db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM run_results rr
            JOIN events e ON e.id = rr.event_id
            JOIN platforms p ON p.id = e.platform_id
            WHERE p.code = 'five_verst' AND e.is_test_event = false
            """
        )
    ).scalar()
    return bool(count)


def _rating_cache_key(limit: int) -> str:
    return f"{RATING_CACHE_KEY_PREFIX}:{limit}"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def _read_rating_cache(limit: int) -> dict[str, object] | None:
    try:
        raw = get_redis_client().get(_rating_cache_key(limit))
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_rating_cache(limit: int, payload: dict[str, object]) -> None:
    try:
        get_redis_client().setex(
            _rating_cache_key(limit),
            RATING_CACHE_TTL_SECONDS,
            json.dumps(payload, default=_json_default, ensure_ascii=False),
        )
    except Exception:
        return


def _compute_five_verst_run_count_rating(db: Session, *, limit: int) -> dict[str, object]:
    bounded_limit = max(1, min(limit, 1000))
    data_source: Literal["lk", "legacy"] = "lk"
    rows: list[dict[str, object]] = []
    latest_event_date: date | None = None
    data_updated_at: datetime | None = None

    if _lk_has_data(db):
        rows = _load_from_lk(db, limit=bounded_limit)
        latest_event_date = _lk_latest_event_date(db)
        data_updated_at = _lk_data_updated_at(db)
    elif resolve_legacy_url():
        with legacy_connection() as conn:
            rows = _load_from_legacy(conn, limit=bounded_limit)
            latest_event_date = _legacy_latest_event_date(conn)
            data_updated_at = _legacy_data_updated_at(conn)
        data_source = "legacy"

    return {
        "title": RATING_TITLE,
        "description": RATING_DESCRIPTION,
        "platform_code": "five_verst",
        "data_source": data_source,
        "latest_event_date": latest_event_date,
        "data_updated_at": data_updated_at,
        "rows": rows,
        "cached_at": None,
    }


def get_five_verst_run_count_rating(
    db: Session,
    *,
    limit: int = 1000,
    use_cache: bool = True,
) -> dict[str, object]:
    bounded_limit = max(1, min(limit, 1000))
    if use_cache:
        cached = _read_rating_cache(bounded_limit)
        if cached is not None:
            return cached

    payload = _compute_five_verst_run_count_rating(db, limit=bounded_limit)
    payload["cached_at"] = datetime.now(timezone.utc).isoformat()
    if use_cache:
        _write_rating_cache(bounded_limit, payload)
    return payload
