#!/usr/bin/env python3
"""One-time backfill: recalculate gender_position for existing parkrun run_results.

Parkrun sync never populated run_results.gender_position (no gender source
was known at the time — see gender_position_service.py). After the
participants.age_category profile backfill (real "SM30-34"-style categories,
gender is the 2nd letter), the code now derives gender for parkrun from
participants.age_category. New syncs pick this up automatically
(app/sync/upsert.py calls recalculate_event_gender_positions for parkrun
imports going forward) — this script is only for events synced before that.

Calling recalculate_event_gender_positions() once per event (~139k events)
does 1-2 DB round trips each — fine on localhost, painfully slow over the
SSH tunnel used for prod access (~0.3s/round-trip -> ~13h for the full run).
This script instead processes events in large chunks: one query to pull all
run_results for a chunk of events, one query for the participants' gender,
group-by in Python, then a single bulk UPDATE per chunk.
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

from app.db.session import get_session_factory
from app.models import Event, Participant, Platform, RunResult
from app.services.gender_position_service import gender_from_age_category


def _chunk(items: list[UUID], size: int) -> list[list[UUID]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill parkrun gender_position in DB (bulk)")
    parser.add_argument("--chunk-size", type=int, default=2000, help="Events per DB round-trip (default: 2000)")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N events (for a dry run)")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == "parkrun").one()
        query = db.query(Event.id).filter(Event.platform_id == platform.id).order_by(Event.event_date)
        if args.limit:
            query = query.limit(args.limit)
        event_ids = [row[0] for row in query.all()]

        total = len(event_ids)
        print(f"parkrun events to process: {total}")
        started = time.monotonic()
        updated_rows = 0
        for chunk_index, chunk in enumerate(_chunk(event_ids, args.chunk_size), start=1):
            results = (
                db.query(RunResult.id, RunResult.event_id, RunResult.participant_id, RunResult.position)
                .filter(RunResult.event_id.in_(chunk))
                .all()
            )
            participant_ids = {row.participant_id for row in results if row.participant_id is not None}
            age_categories: dict[UUID, str | None] = {}
            if participant_ids:
                age_categories = dict(
                    db.query(Participant.id, Participant.age_category).filter(Participant.id.in_(participant_ids)).all()
                )

            by_event: dict[UUID, list] = {}
            for row in results:
                by_event.setdefault(row.event_id, []).append(row)

            mappings: list[dict[str, object]] = []
            for rows in by_event.values():
                genders: dict[UUID, str | None] = {}
                for row in rows:
                    age_category = age_categories.get(row.participant_id) if row.participant_id else None
                    # Формат тот же, что у runpark: 2-я буква категории — M/W.
                    genders[row.id] = gender_from_age_category("runpark", age_category)

                ranked = sorted(
                    (row for row in rows if genders[row.id] is not None and row.position is not None),
                    key=lambda row: row.position,
                )
                counters = {"male": 0, "female": 0}
                for row in ranked:
                    gender = genders[row.id]
                    counters[gender] += 1  # type: ignore[index]
                    mappings.append({"id": row.id, "gender_position": counters[gender]})  # type: ignore[index]

            if mappings:
                db.bulk_update_mappings(RunResult, mappings)
            db.commit()
            updated_rows += len(mappings)

            processed_events = min(chunk_index * args.chunk_size, total)
            elapsed = time.monotonic() - started
            print(
                f"  {processed_events}/{total} events, {updated_rows} rows updated ({elapsed:.0f}s elapsed)",
                flush=True,
            )
        print("done")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
