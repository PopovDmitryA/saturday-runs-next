from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, Platform, ProtocolSyncState
from app.sync.s95_reconcile import (
    ReconcileProtocolsOptions,
    ReconcileReason,
    _classify_reconcile_reason,
    reconcile_stale_protocols,
)


def test_classify_reconcile_reason_never_checked() -> None:
    summary = SimpleNamespace(finishers_count=10)
    reason = _classify_reconcile_reason(
        summary,
        None,
        run_count=10,
        check_cutoff=datetime.now(timezone.utc),
    )
    assert reason == ReconcileReason.never_checked


def test_classify_reconcile_reason_count_mismatch() -> None:
    summary = SimpleNamespace(finishers_count=10)
    state = SimpleNamespace(
        last_protocol_check_at=datetime.now(timezone.utc),
        finishers_at_fetch=10,
    )
    reason = _classify_reconcile_reason(
        summary,
        state,
        run_count=8,
        check_cutoff=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    assert reason == ReconcileReason.count_mismatch


def test_reconcile_dry_run_returns_candidates(db_session: Session) -> None:
    result = reconcile_stale_protocols(
        db_session,
        ReconcileProtocolsOptions(dry_run=True, limit=5),
    )
    assert result.candidates_total <= 5
    assert result.protocols_fetched == 0
    assert len(result.planned) == result.candidates_total


def test_plan_finds_never_checked_s95_protocol(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        pytest.skip("s95 platform not seeded")

    slug = f"reconcile-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Reconcile Park",
    )
    db_session.add(location)
    db_session.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:2099-01-01",
        event_date=__import__("datetime").date(2099, 1, 1),
        event_number=9999,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=platform.id,
            location_id=location.id,
            event_id=event.id,
            external_event_key=event.external_event_key,
            event_date=event.event_date,
            event_number=9999,
            finishers_count=10,
            volunteers_count=2,
            summary_hash="reconcile-hash",
            source_url="https://s95.ru/activities/9999",
        )
    )
    db_session.commit()

    from app.sync.s95_reconcile import plan_stale_protocol_reconcile

    candidates = plan_stale_protocol_reconcile(db_session, limit=50, location_slug=slug)
    keys = {item.external_event_key for item in candidates}
    assert event.external_event_key in keys
