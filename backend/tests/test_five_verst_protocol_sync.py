from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Platform
from app.sync.five_verst_latest import (
    LatestResultAction,
    LatestResultPlanItem,
    _plan_protocol_queue,
)
from app.sync.global_sync import _select_summaries_for_protocol_fetch


def _summary_stub(*, key: str, action: LatestResultAction) -> LatestResultPlanItem:
    return LatestResultPlanItem(
        summary=SimpleNamespace(external_event_key=key),
        action=action,
    )


def test_plan_protocol_queue_prioritizes_changed_and_missing() -> None:
    items = [
        _summary_stub(key="new-1", action=LatestResultAction.new_summary),
        _summary_stub(key="changed-1", action=LatestResultAction.changed_summary),
        _summary_stub(key="missing-1", action=LatestResultAction.missing_protocol),
        _summary_stub(key="new-2", action=LatestResultAction.new_summary),
        _summary_stub(key="new-3", action=LatestResultAction.new_summary),
    ]
    queue = _plan_protocol_queue(items, protocol_fetch_limit=2, fetch_all_protocols_on_change=True)
    assert [item.summary.external_event_key for item in queue] == [
        "changed-1",
        "missing-1",
    ]


def test_plan_protocol_queue_unlimited() -> None:
    items = [
        _summary_stub(key=f"new-{index}", action=LatestResultAction.new_summary)
        for index in range(5)
    ]
    queue = _plan_protocol_queue(items, protocol_fetch_limit=None, fetch_all_protocols_on_change=True)
    assert len(queue) == 5


def test_plan_protocol_queue_respects_limit_without_fetch_all() -> None:
    items = [
        _summary_stub(key="changed-1", action=LatestResultAction.changed_summary),
        _summary_stub(key="new-1", action=LatestResultAction.new_summary),
        _summary_stub(key="new-2", action=LatestResultAction.new_summary),
    ]
    queue = _plan_protocol_queue(items, protocol_fetch_limit=2, fetch_all_protocols_on_change=False)
    assert [item.summary.external_event_key for item in queue] == ["changed-1", "new-1"]


def test_select_summaries_for_protocol_fetch_all_changed() -> None:
    rows = [("a", object()), ("b", object()), ("c", object())]
    selected = _select_summaries_for_protocol_fetch(
        rows,
        protocol_fetch_limit=1,
        fetch_all_protocols_on_change=True,
    )
    assert selected == rows


def test_fetch_and_upsert_event_protocol_updates_participant_fields(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    from app.models import Event, Location, Participant, RunResult
    from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalRunResult
    from app.sync import upsert
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol

    slug = f"protocol-upsert-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Protocol Upsert Park",
        source_url=f"https://5verst.ru/{slug}/",
    )
    db_session.add(location)
    db_session.flush()

    summary = CanonicalEventSummary(
        external_event_key=f"{slug}:2026-05-23:1",
        event_date=date(2026, 5, 23),
        event_number=1,
        location_external_key=slug,
        location_name=location.name,
        finishers_count=1,
        volunteers_count=0,
        source_url=f"https://5verst.ru/{slug}/results/23.05.2026/",
        summary_hash="hash-1",
    )
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)

    run_result = CanonicalRunResult(
        external_result_key=f"{slug}:2026-05-23:790000001",
        event_date=date(2026, 5, 23),
        external_user_id="790000001",
        participant_name="Test Runner",
        position=1,
        finish_time_sec=1200,
        finish_time_display="00:20:00",
        age_category="М30-34 (1)",
        club_name="Test Club",
        is_pr=True,
        is_first_run=False,
        is_first_run_at_location=True,
        achievement_labels=["Первый финиш на Protocol Upsert Park"],
        location_external_key=slug,
        event_number=1,
    )

    with (
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_run_protocol",
            return_value=([run_result], "<html></html>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_volunteers",
            return_value=([], "<html></html>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.source_hash",
            return_value="protocol-hash",
        ),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)

    participant = (
        db_session.query(Participant)
        .filter(Participant.platform_id == platform.id, Participant.external_user_id == "790000001")
        .one()
    )
    assert participant.age_category == "М30-34 (1)"
    assert participant.club_name == "Test Club"

    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    run = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert run.is_first_run_at_location is True
    assert run.club_name == "Test Club"

    db_session.refresh(summary_row)
    assert summary_row.event_id == event.id


def test_classify_reconcile_reason_never_checked() -> None:
    from app.sync.five_verst_reconcile import ReconcileReason, _classify_reconcile_reason

    summary = SimpleNamespace(finishers_count=10)
    reason = _classify_reconcile_reason(
        summary,
        None,
        run_count=10,
        check_cutoff=datetime.now(timezone.utc),
    )
    assert reason == ReconcileReason.never_checked


def test_classify_reconcile_reason_count_mismatch() -> None:
    from app.sync.five_verst_reconcile import ReconcileReason, _classify_reconcile_reason

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
    from app.sync.five_verst_reconcile import ReconcileProtocolsOptions, reconcile_stale_protocols

    result = reconcile_stale_protocols(
        db_session,
        ReconcileProtocolsOptions(dry_run=True, limit=5),
    )
    assert result.candidates_total <= 5
    assert result.protocols_fetched == 0
    assert len(result.planned) == result.candidates_total
