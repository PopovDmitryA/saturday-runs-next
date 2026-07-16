#!/usr/bin/env python3
"""Убрать место в возрастной группе из age_category для платформы five_verst.

Протокол 5 вёрст печатает в ячейке категории место в этой группе на забеге:
«М40-44 (2)». Парсер (bulk_parser._parse_age_category_from_row) до фикса писал
строку целиком, поэтому в run_results.age_category каждое место образует «свою»
категорию: 'М35-39 (1)', 'М35-39 (2)', ... — любая группировка по age_category
для 5 вёрст бессмысленна.

Скрипт срезает хвост '(N)' у run_results и participants платформы five_verst.
Идемпотентен: чистые значения ('М40-44') под фильтр не попадают.

Запуск:
    docker compose exec api python scripts/backfill_five_verst_age_category.py            # dry-run
    docker compose exec api python scripts/backfill_five_verst_age_category.py --apply
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_session_factory

PLATFORM_CODE = "five_verst"
# Хвост «(2)» в конце значения. Тот же паттерн, что и в
# app/platform_adapters/five_verst/age_category.py, но на стороне PG.
DIRTY_RE = r"\(\d+\)\s*$"
CLEAN_SQL = r"regexp_replace(age_category, '\s*\(\d+\)\s*$', '')"

COUNT_RUNS_SQL = f"""
SELECT count(*)
FROM run_results rr
JOIN events e ON e.id = rr.event_id
WHERE e.platform_id = :platform_id
  AND rr.age_category ~ '{DIRTY_RE}'
"""

# Keyset по pk: каждый батч — ограниченный проход по индексу, без seq scan
# по 1.2М строк на каждой итерации.
BATCH_SQL = f"""
WITH batch AS (
    SELECT rr.id
    FROM run_results rr
    WHERE rr.id > :last_id
    ORDER BY rr.id
    LIMIT :batch_size
),
target AS (
    SELECT b.id
    FROM batch b
    JOIN run_results rr ON rr.id = b.id
    JOIN events e ON e.id = rr.event_id
    WHERE e.platform_id = :platform_id
      AND rr.age_category ~ '{DIRTY_RE}'
),
updated AS (
    UPDATE run_results rr
    SET age_category = NULLIF({CLEAN_SQL}, '')
    FROM target t
    WHERE rr.id = t.id
    RETURNING 1
)
SELECT
    -- max(uuid) в PG нет; batch уже отсортирован по id, берём последнюю строку.
    (SELECT id FROM batch ORDER BY id DESC LIMIT 1) AS next_cursor,
    (SELECT count(*) FROM batch) AS scanned,
    (SELECT count(*) FROM updated) AS updated
"""

PARTICIPANTS_COUNT_SQL = f"""
SELECT count(*) FROM participants
WHERE platform_id = :platform_id AND age_category ~ '{DIRTY_RE}'
"""

PARTICIPANTS_UPDATE_SQL = f"""
UPDATE participants
SET age_category = NULLIF({CLEAN_SQL}, '')
WHERE platform_id = :platform_id AND age_category ~ '{DIRTY_RE}'
"""

ZERO_UUID = UUID(int=0)


def _platform_id(db: Session) -> UUID:
    row = db.execute(
        text("SELECT id FROM platforms WHERE code = :code"),
        {"code": PLATFORM_CODE},
    ).scalar_one_or_none()
    if row is None:
        raise SystemExit(f"platform {PLATFORM_CODE} not found")
    return row


def backfill_run_results(db: Session, platform_id: UUID, *, batch_size: int) -> int:
    total = 0
    cursor: UUID = ZERO_UUID
    started = time.monotonic()
    while True:
        row = db.execute(
            text(BATCH_SQL),
            {"platform_id": platform_id, "last_id": cursor, "batch_size": batch_size},
        ).one()
        next_cursor, scanned, updated = row
        if scanned == 0:
            break
        db.commit()
        total += updated
        cursor = next_cursor
        if updated:
            print(f"  run_results: {total} обновлено, {time.monotonic() - started:.0f}s", flush=True)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Strip in-group place suffix from five_verst age_category")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--batch-size", type=int, default=20_000, help="Rows scanned per batch (default: 20000)")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform_id = _platform_id(db)
        dirty_runs = db.execute(text(COUNT_RUNS_SQL), {"platform_id": platform_id}).scalar_one()
        dirty_participants = db.execute(text(PARTICIPANTS_COUNT_SQL), {"platform_id": platform_id}).scalar_one()
        print(f"грязных run_results: {dirty_runs}", flush=True)
        print(f"грязных participants: {dirty_participants}", flush=True)

        if not args.apply:
            print("dry-run, ничего не записано (--apply чтобы применить)")
            return 0

        runs_updated = backfill_run_results(db, platform_id, batch_size=args.batch_size)
        participants_updated = db.execute(text(PARTICIPANTS_UPDATE_SQL), {"platform_id": platform_id}).rowcount
        db.commit()
        print(f"готово: run_results={runs_updated} participants={participants_updated}", flush=True)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
