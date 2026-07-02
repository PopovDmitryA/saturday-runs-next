"""Backfill run_results.gender_position для существующих протоколов.

Пол финишёра:
- five_verst — первая буква age_category (М/Ж);
- s95 — participants.profile_extra->platform_codes->gender (male/female);
- parkrun, runpark — не считаем (у runpark категория есть лишь у 41% строк).

Запуск:
    docker compose exec api python scripts/backfill_gender_positions.py
"""

from __future__ import annotations

import sys
import time

from sqlalchemy import text

sys.path.insert(0, "/app")

from app.db.session import get_session_factory  # noqa: E402

AGE_CATEGORY_SQL = """
WITH ranked AS (
    SELECT rr.id,
           rank() OVER (
               PARTITION BY rr.event_id, {gender_expr}
               ORDER BY rr.position
           ) AS gp
    FROM run_results rr
    JOIN events e ON rr.event_id = e.id
    JOIN platforms p ON e.platform_id = p.id
    WHERE p.code = :platform_code
      AND rr.position IS NOT NULL
      AND {gender_expr} IS NOT NULL
)
UPDATE run_results rr
SET gender_position = ranked.gp
FROM ranked
WHERE rr.id = ranked.id
  AND rr.gender_position IS DISTINCT FROM ranked.gp
"""

FIVE_VERST_GENDER = "(CASE left(rr.age_category, 1) WHEN 'М' THEN 'male' WHEN 'Ж' THEN 'female' END)"

CLEAR_SQL = """
UPDATE run_results rr
SET gender_position = NULL
FROM events e
JOIN platforms p ON e.platform_id = p.id
WHERE rr.event_id = e.id
  AND p.code = :platform_code
  AND rr.gender_position IS NOT NULL
"""

S95_SQL = """
WITH ranked AS (
    SELECT rr.id,
           rank() OVER (
               PARTITION BY rr.event_id, pa.profile_extra->'platform_codes'->>'gender'
               ORDER BY rr.position
           ) AS gp
    FROM run_results rr
    JOIN events e ON rr.event_id = e.id
    JOIN platforms p ON e.platform_id = p.id
    JOIN participants pa ON rr.participant_id = pa.id
    WHERE p.code = 's95'
      AND rr.position IS NOT NULL
      AND pa.profile_extra->'platform_codes'->>'gender' IN ('male', 'female')
)
UPDATE run_results rr
SET gender_position = ranked.gp
FROM ranked
WHERE rr.id = ranked.id
  AND rr.gender_position IS DISTINCT FROM ranked.gp
"""


def main() -> None:
    db = get_session_factory()()
    steps = [
        ("five_verst", text(AGE_CATEGORY_SQL.format(gender_expr=FIVE_VERST_GENDER)), {"platform_code": "five_verst"}),
        ("s95", text(S95_SQL), {}),
        ("runpark (clear)", text(CLEAR_SQL), {"platform_code": "runpark"}),
    ]
    for name, sql, params in steps:
        started = time.monotonic()
        result = db.execute(sql, params)
        db.commit()
        print(f"{name}: updated {result.rowcount} rows in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
