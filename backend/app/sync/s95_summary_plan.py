from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.models import EventSummary, Platform
from app.platform_adapters.canonical import CanonicalEventSummary
from app.sync import upsert

PLATFORM_CODE = "s95"


class SummarySyncAction(str, Enum):
    unchanged = "unchanged"
    new_summary = "new_summary"
    changed_summary = "changed_summary"
    missing_protocol = "missing_protocol"


@dataclass(frozen=True)
class SummarySyncPlanItem:
    summary: CanonicalEventSummary
    action: SummarySyncAction
    location_id: str | None = None


def classify_event_summary(
    db: Session,
    platform: Platform,
    summary: CanonicalEventSummary,
) -> SummarySyncPlanItem:
    row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.external_event_key == summary.external_event_key,
        )
        .one_or_none()
    )
    if row is None:
        return SummarySyncPlanItem(summary=summary, action=SummarySyncAction.new_summary)
    if row.summary_hash != summary.summary_hash:
        return SummarySyncPlanItem(
            summary=summary,
            action=SummarySyncAction.changed_summary,
            location_id=str(row.location_id),
        )
    if row.event_id is None:
        return SummarySyncPlanItem(
            summary=summary,
            action=SummarySyncAction.missing_protocol,
            location_id=str(row.location_id),
        )
    return SummarySyncPlanItem(
        summary=summary,
        action=SummarySyncAction.unchanged,
        location_id=str(row.location_id),
    )


def plan_event_summaries_sync(
    db: Session,
    summaries: list[CanonicalEventSummary],
    *,
    platform_code: str = PLATFORM_CODE,
) -> list[SummarySyncPlanItem]:
    platform = upsert.get_platform(db, platform_code)
    return [classify_event_summary(db, platform, summary) for summary in summaries]


def plan_protocol_queue(
    items: list[SummarySyncPlanItem],
    *,
    protocol_fetch_limit: int,
    fetch_all_protocols_on_change: bool,
) -> list[SummarySyncPlanItem]:
    if protocol_fetch_limit <= 0:
        return []
    priority = [
        item
        for item in items
        if item.action in (SummarySyncAction.changed_summary, SummarySyncAction.missing_protocol)
    ]
    regular = [item for item in items if item.action == SummarySyncAction.new_summary]
    if fetch_all_protocols_on_change:
        return priority + regular[:protocol_fetch_limit]
    combined = priority + regular
    return combined[:protocol_fetch_limit]
