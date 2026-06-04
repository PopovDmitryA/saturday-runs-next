#!/usr/bin/env python3
"""Mac-side worker: polls Redis for admin jobs and processes parkrun queue via Chrome CDP."""
from __future__ import annotations

import sys
import time
from pathlib import Path

if sys.version_info < (3, 12):
    print("Needs Python 3.12+", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.parkrun_local_worker import (
    run_pending_parkrun_batch,
    should_process_requested_job,
    touch_heartbeat,
)


def main() -> int:
    poll_seconds = 2.0
    print("parkrun local worker: waiting for admin jobs (Ctrl+C to stop)")
    print("Enable CDP: PARKRUN_USE_CDP_FOR_FETCH=true PARKRUN_CDP_URL=http://127.0.0.1:9222")
    Session = get_session_factory()
    while True:
        touch_heartbeat()
        if should_process_requested_job():
            print("Processing parkrun pending queue…")
            with Session() as db:
                result = run_pending_parkrun_batch(db, limit=20, reset_failed=True)
            requeued_failed = result.get("requeued_failed", 0)
            requeued_stuck = result.get("requeued_stuck_done", 0)
            total = result.get("total", 0)
            if requeued_failed or requeued_stuck:
                print(
                    f"Requeued: failed={requeued_failed}, "
                    f"stuck done (no link)={requeued_stuck}"
                )
            print("Summary:", result.get("summary"))
            if total == 0 and not result.get("summary"):
                print(
                    "  (очередь пуста — в админке «Очередь (pending): 0»? "
                    "Повторите привязку профиля в Настройках или проверьте stuck done в статусе)"
                )
            for line in result.get("details", []):
                print(" ", line)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        raise SystemExit(0) from None
