#!/usr/bin/env python3
"""
One-time full import of RunPark events + results + volunteering from 2022-01-01.

Usage:
    docker compose exec api python scripts/runpark_initial_import.py
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    from app.db.session import get_session_factory
    from app.sync.runpark_global_sync import sync_runpark_batch

    since = date(2022, 1, 1)
    logger.info("Starting RunPark full import since %s", since)

    Session = get_session_factory()
    with Session() as db:
        result = sync_runpark_batch(db, since_date=since)

    logger.info(
        "Done. Events: %d/%d, runs: %d, volunteers: %d, errors: %d",
        result.events_upserted,
        result.events_total,
        result.run_results_upserted,
        result.volunteer_results_upserted,
        len(result.errors),
    )
    if result.errors:
        logger.warning("Errors:\n%s", "\n".join(result.errors))
        sys.exit(1)


if __name__ == "__main__":
    main()
