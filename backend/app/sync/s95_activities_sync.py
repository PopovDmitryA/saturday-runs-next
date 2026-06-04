from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.sync.s95_latest import S95LatestSyncOptions, S95LatestSyncResult, sync_s95_latest

logger = logging.getLogger(__name__)


@dataclass
class ActivitiesSyncResult:
    rows_total: int = 0
    summaries_upserted: int = 0
    summaries_unchanged: int = 0
    protocols_fetched: int = 0
    run_results_upserted: int = 0
    volunteer_results_upserted: int = 0
    errors: list[str] = field(default_factory=list)


def _map_result(result: S95LatestSyncResult) -> ActivitiesSyncResult:
    return ActivitiesSyncResult(
        rows_total=result.summaries_total,
        summaries_upserted=result.summaries_upserted,
        summaries_unchanged=result.unchanged,
        protocols_fetched=result.protocols_fetched,
        run_results_upserted=result.run_results_upserted,
        volunteer_results_upserted=result.volunteer_results_upserted,
        errors=result.errors,
    )


def sync_recent_activities(
    db: Session,
    *,
    protocol_fetch_limit: int = 2,
    fetch_all_protocols_on_change: bool = True,
) -> ActivitiesSyncResult:
    """Backward-compatible wrapper around sync_s95_latest."""
    result = sync_s95_latest(
        db,
        S95LatestSyncOptions(
            protocol_fetch_limit=protocol_fetch_limit,
            fetch_all_protocols_on_change=fetch_all_protocols_on_change,
        ),
    )
    return _map_result(result)
