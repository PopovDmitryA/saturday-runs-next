#!/usr/bin/env python3
"""Delete all run_results for an S95 athlete (e.g. after duplicate import)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Event, Participant, Platform, RunResult


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("external_user_id", help="S95 athlete id, e.g. 5207")
    args = parser.parse_args()

    db = get_session_factory()()
    platform = db.query(Platform).filter(Platform.code == "s95").one()
    participant = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == args.external_user_id,
        )
        .one_or_none()
    )
    if participant is None:
        print(f"No S95 participant {args.external_user_id}")
        db.close()
        return 1

    q = db.query(RunResult).filter(RunResult.participant_id == participant.id)
    count = q.count()
    event_ids = {row.event_id for row in q.all() if row.event_id}
    q.delete(synchronize_session=False)
    db.flush()

    orphan_events = 0
    for event_id in event_ids:
        if event_id is None:
            continue
        remaining = db.query(RunResult).filter(RunResult.event_id == event_id).count()
        if remaining == 0:
            db.query(Event).filter(Event.id == event_id).delete()
            orphan_events += 1

    db.commit()
    print(
        f"Deleted {count} run_results for athlete {args.external_user_id}, "
        f"removed {orphan_events} orphan events"
    )
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
