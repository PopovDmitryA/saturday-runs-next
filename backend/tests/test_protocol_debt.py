"""Долг протокола: саммари уже новое, а протокол под ним — от прошлой версии.

Сценарий из жизни (Серов, parkdkm, 22.08.2026): в 09:12 скачали протокол, где
победитель с 00:15:04; в 17:00 площадка исправила протокол, прогон записал
новое саммари (лучшее мужское 00:21:55) и упал, не дойдя до протокола в
очереди. Все следующие прогоны видели совпадающий summary_hash и считали
площадку `unchanged` — сайт трое суток показывал 00:15:04.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform, ProtocolSyncState
from app.sync.five_verst_latest import LatestResultAction, _classify_summary
from app.sync.protocol_debt import protocol_is_stale


def test_protocol_is_stale_when_summary_moved_on() -> None:
    summary_row = SimpleNamespace(event_id=uuid4(), summary_hash="hash-after-fix")
    state = SimpleNamespace(summary_hash_at_fetch="hash-before-fix")
    assert protocol_is_stale(state, summary_row) is True


def test_protocol_is_not_stale_when_hashes_match() -> None:
    summary_row = SimpleNamespace(event_id=uuid4(), summary_hash="hash-1")
    state = SimpleNamespace(summary_hash_at_fetch="hash-1")
    assert protocol_is_stale(state, summary_row) is False


def test_protocol_without_state_is_not_stale() -> None:
    """Нет расписки — «не знаем», а не «долг».

    Так выглядят строки от mark_protocol_check по удалённой (404) странице:
    считать их долгом нельзя, иначе очередь забьётся протоколами, которых
    на сайте нет.
    """
    summary_row = SimpleNamespace(event_id=uuid4(), summary_hash="hash-1")
    assert protocol_is_stale(None, summary_row) is False
    assert protocol_is_stale(SimpleNamespace(summary_hash_at_fetch=None), summary_row) is False


def test_summary_without_event_is_not_stale() -> None:
    """Протокола не было вовсе — это missing_protocol, отдельная ветка."""
    summary_row = SimpleNamespace(event_id=None, summary_hash="hash-1")
    assert protocol_is_stale(SimpleNamespace(summary_hash_at_fetch="other"), summary_row) is False


def _seed_serov_case(db_session: Session, platform: Platform):
    """Площадка с закачанным протоколом; расписку ставит сама закачка."""
    from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalRunResult
    from app.sync import upsert
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol

    slug = f"debt-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Debt Park",
        source_url=f"https://5verst.ru/{slug}/",
    )
    db_session.add(location)
    db_session.flush()

    summary = CanonicalEventSummary(
        external_event_key=f"{slug}:2026-08-22:1",
        event_date=date(2026, 8, 22),
        event_number=1,
        location_external_key=slug,
        location_name=location.name,
        finishers_count=1,
        volunteers_count=0,
        best_male_time_sec=904,
        best_male_time_display="00:15:04",
        source_url=f"https://5verst.ru/{slug}/results/22.08.2026/",
        summary_hash="hash-before-fix",
    )
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)

    run_result = CanonicalRunResult(
        external_result_key=f"{slug}:2026-08-22:790000042",
        event_date=date(2026, 8, 22),
        external_user_id="790000042",
        participant_name="Debt Runner",
        position=1,
        finish_time_sec=904,
        finish_time_display="00:15:04",
        location_external_key=slug,
        event_number=1,
    )

    with (
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
            return_value=([run_result], [], "<html></html>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.source_hash",
            return_value="protocol-hash",
        ),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)

    return slug, location, summary, summary_row


def test_successful_fetch_records_summary_hash(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    _, _, summary, summary_row = _seed_serov_case(db_session, platform)

    state = (
        db_session.query(ProtocolSyncState)
        .filter(ProtocolSyncState.event_id == summary_row.event_id)
        .one()
    )
    assert state.summary_hash_at_fetch == summary.summary_hash


def test_classify_summary_returns_stale_protocol_after_lost_trigger(db_session: Session) -> None:
    """Саммари обновилось, протокол не доехал — следующий прогон должен это увидеть."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    _, _, summary, summary_row = _seed_serov_case(db_session, platform)

    # Имитируем упавший прогон: новый summary_hash закоммичен, протокол не скачан.
    summary_row.summary_hash = "hash-after-fix"
    summary_row.best_male_time_sec = 1315
    summary_row.best_male_time_display = "00:21:55"
    db_session.flush()

    fixed = SimpleNamespace(
        external_event_key=summary.external_event_key,
        summary_hash="hash-after-fix",
    )
    item = _classify_summary(db_session, platform, fixed)
    assert item.action == LatestResultAction.stale_protocol

    # А когда протокол перечитан — долг закрыт и площадка снова `unchanged`.
    state = (
        db_session.query(ProtocolSyncState)
        .filter(ProtocolSyncState.event_id == summary_row.event_id)
        .one()
    )
    state.summary_hash_at_fetch = "hash-after-fix"
    state.last_protocol_fetched_at = datetime.now(timezone.utc)
    db_session.flush()

    assert _classify_summary(db_session, platform, fixed).action == LatestResultAction.unchanged


def test_stale_protocol_jumps_the_protocol_queue() -> None:
    from app.sync.five_verst_latest import LatestResultPlanItem, _plan_protocol_queue

    def stub(key: str, action: LatestResultAction) -> LatestResultPlanItem:
        return LatestResultPlanItem(summary=SimpleNamespace(external_event_key=key), action=action)

    items = [
        stub("new-1", LatestResultAction.new_summary),
        stub("stale-1", LatestResultAction.stale_protocol),
        stub("new-2", LatestResultAction.new_summary),
    ]
    queue = _plan_protocol_queue(items, protocol_fetch_limit=1, fetch_all_protocols_on_change=True)
    assert [item.summary.external_event_key for item in queue] == ["stale-1"]
