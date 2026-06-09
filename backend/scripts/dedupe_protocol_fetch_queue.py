#!/usr/bin/env python3
"""Remove duplicate fetch_protocol_from_profile tasks from Celery Redis queues."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.celery_queue_inspector import FIVE_VERST_BATCH_QUEUE, S95_BATCH_QUEUE
from app.services.protocol_queue_dedup import dedupe_protocol_fetch_queue


def main() -> int:
    parser = argparse.ArgumentParser(description="Dedupe protocol fetch tasks in Celery queues")
    parser.add_argument(
        "--platform",
        choices=["five_verst", "s95", "all"],
        default="all",
        help="Which platform queue to dedupe",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite the queue (default is dry-run)",
    )
    args = parser.parse_args()

    targets: list[tuple[str, str]] = []
    if args.platform in {"five_verst", "all"}:
        targets.append(("five_verst", FIVE_VERST_BATCH_QUEUE))
    if args.platform in {"s95", "all"}:
        targets.append(("s95", S95_BATCH_QUEUE))

    for platform_code, queue_name in targets:
        stats = dedupe_protocol_fetch_queue(
            queue_name,
            platform_code=platform_code,
            dry_run=not args.apply,
        )
        mode = "applied" if args.apply else "dry-run"
        print(
            f"{platform_code} ({queue_name}) [{mode}]: "
            f"total={stats['total']} kept={stats['kept']} removed={stats['removed']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
