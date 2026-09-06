"""Сверка протоколов: расхождения идут вне очереди и мимо фильтра по давности.

До этого reconcile брал 100 самых давно не проверявшихся протоколов и только
потом считал причину — то есть причина ни на что не влияла. Плюс фильтр
`min_check_interval_days=7` прятал именно свежие расхождения: протокол,
который мы читали сегодня утром, считался «проверенным» на неделю вперёд.
Серов 22.08.2026 попал ровно в эту дыру.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, ProtocolSyncState, RunResult
from app.sync.five_verst_reconcile import (
    PRIORITY_REASONS,
    ReconcileReason,
    _claimed_fastest_sec,
    _classify_reconcile_reason,
    plan_stale_protocol_reconcile,
)

NOW = datetime.now(timezone.utc)


def _state(**kwargs: object) -> SimpleNamespace:
    base = {
        "last_protocol_check_at": NOW,
        "summary_hash_at_fetch": "hash-1",
        "finishers_at_fetch": 38,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _summary(**kwargs: object) -> SimpleNamespace:
    base = {
        "summary_hash": "hash-1",
        "finishers_count": 38,
        "volunteers_count": 12,
        "best_male_time_sec": 1315,
        "best_female_time_sec": 1601,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_claimed_fastest_ignores_zero_sentinel() -> None:
    """0 в лучшем времени — «в этом зачёте никого не было», а не результат."""
    assert _claimed_fastest_sec(_summary(best_male_time_sec=1315, best_female_time_sec=0)) == 1315
    assert _claimed_fastest_sec(_summary(best_male_time_sec=0, best_female_time_sec=0)) is None


def test_time_mismatch_when_counts_agree() -> None:
    """Случай Серова: финишёров столько же, а победитель другой."""
    reason = _classify_reconcile_reason(
        _summary(),
        _state(),
        run_count=38,
        check_cutoff=NOW,
        fastest_stored_sec=904,
    )
    assert reason == ReconcileReason.time_mismatch


def test_no_mismatch_when_times_agree() -> None:
    reason = _classify_reconcile_reason(
        _summary(),
        _state(),
        run_count=38,
        check_cutoff=NOW,
        fastest_stored_sec=1315,
    )
    assert reason == ReconcileReason.check_due


def test_protocol_debt_wins_over_other_reasons() -> None:
    reason = _classify_reconcile_reason(
        _summary(summary_hash="hash-2"),
        _state(summary_hash_at_fetch="hash-1"),
        run_count=38,
        check_cutoff=NOW,
        fastest_stored_sec=904,
    )
    assert reason == ReconcileReason.protocol_debt


def test_count_mismatch_is_not_a_priority_reason() -> None:
    """Расхождение по количеству перекачкой не лечится и в приоритет не идёт.

    У 5 вёрст таких стартов под две сотни (схлопнутые дубли, «НЕИЗВЕСТНЫЙ»
    без времени). В приоритетной пачке они встали бы навсегда.
    """
    reason = _classify_reconcile_reason(
        _summary(),
        _state(),
        run_count=37,
        check_cutoff=NOW,
        fastest_stored_sec=1315,
    )
    assert reason == ReconcileReason.count_mismatch
    assert reason not in PRIORITY_REASONS


def _seed_event(
    db: Session,
    platform: Platform,
    *,
    label: str,
    fastest_sec: int,
    claimed_male_sec: int,
    checked_days_ago: int,
    fetched_hours_ago: float | None = None,
) -> str:
    from app.platform_adapters.canonical import CanonicalEventSummary
    from app.sync import upsert

    slug = f"{label}-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name=f"Park {label}",
        source_url=f"https://5verst.ru/{slug}/",
    )
    db.add(location)
    db.flush()

    summary = CanonicalEventSummary(
        external_event_key=f"{slug}:2026-08-22:1",
        event_date=date(2026, 8, 22),
        event_number=1,
        location_external_key=slug,
        location_name=location.name,
        finishers_count=1,
        volunteers_count=0,
        best_male_time_sec=claimed_male_sec,
        best_male_time_display="00:21:55",
        source_url=f"https://5verst.ru/{slug}/results/22.08.2026/",
        summary_hash=f"hash-{label}",
    )
    summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=summary.external_event_key,
        event_date=summary.event_date,
        event_number=1,
    )
    db.add(event)
    db.flush()
    summary_row.event_id = event.id

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"{label}-{uuid4().hex[:6]}",
        display_name=f"Runner {label}",
    )
    db.add(participant)
    db.flush()
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{slug}:2026-08-22:1",
            position=1,
            finish_time_sec=fastest_sec,
            finish_time_display="00:15:04",
        )
    )
    fetched_at = (
        NOW - timedelta(hours=fetched_hours_ago)
        if fetched_hours_ago is not None
        else NOW - timedelta(days=checked_days_ago)
    )
    db.add(
        ProtocolSyncState(
            event_id=event.id,
            event_summary_id=summary_row.id,
            last_protocol_fetched_at=fetched_at,
            last_protocol_check_at=NOW - timedelta(days=checked_days_ago),
            summary_hash_at_fetch=summary_row.summary_hash,
            finishers_at_fetch=summary_row.finishers_count,
            run_results_count=1,
        )
    )
    db.flush()
    return summary.external_event_key


def test_fresh_time_mismatch_beats_the_age_rotation(db_session: Session) -> None:
    """Расхождение, найденное сегодня, чинится сегодня — а не через неделю."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    # Ровно случай Серова: протокол скачали сегодня утром, поэтому фильтр по
    # давности считает его проверенным на неделю вперёд. Но площадка правила
    # протокол уже после нашей закачки, и время лидера у нас (904) не то, что
    # обещает сводка (1315).
    mismatched = _seed_event(
        db_session,
        platform,
        label="mismatch",
        fastest_sec=904,
        claimed_male_sec=1315,
        checked_days_ago=0,
        fetched_hours_ago=8,
    )
    # Проверен давно, но с сайтом сходится — обычный кандидат ротации.
    consistent = _seed_event(
        db_session,
        platform,
        label="consistent",
        fastest_sec=1315,
        claimed_male_sec=1315,
        checked_days_ago=90,
    )

    plan = plan_stale_protocol_reconcile(
        db_session,
        limit=50,
        min_check_interval_days=7,
        mismatch_retry_interval_hours=6,
    )
    keys = [item.external_event_key for item in plan]

    assert mismatched in keys, "свежее расхождение обязано попасть в план вопреки фильтру давности"
    by_key = {item.external_event_key: item for item in plan}
    assert by_key[mismatched].reason == ReconcileReason.time_mismatch
    # Не «строго первый»: в базе могут лежать другие расхождения (в dev-БД это
    # реальные площадки), и порядок внутри приоритета — по дате старта. Важно
    # ровно одно — что этот протокол попал в приоритет, а не в общую ротацию.
    assert by_key[mismatched].reason in PRIORITY_REASONS
    # Давно не проверявшийся, но целый протокол — в плане есть, но позже.
    if consistent in keys:
        assert keys.index(consistent) > keys.index(mismatched)
        assert by_key[consistent].reason not in PRIORITY_REASONS


def test_mismatch_retry_interval_stops_the_loop(db_session: Session) -> None:
    """Расхождение, которое не вылечилось перекачкой, не гоняем каждые три часа."""
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        pytest.skip("five_verst platform not seeded")

    mismatched = _seed_event(
        db_session,
        platform,
        label="looping",
        fastest_sec=904,
        claimed_male_sec=1315,
        checked_days_ago=0,
        fetched_hours_ago=8,
    )

    def priority_keys(retry_hours: int) -> list[str]:
        plan = plan_stale_protocol_reconcile(
            db_session,
            limit=50,
            min_check_interval_days=7,
            mismatch_retry_interval_hours=retry_hours,
        )
        return [item.external_event_key for item in plan if item.reason in PRIORITY_REASONS]

    assert mismatched in priority_keys(6)

    # Перекачали — и расхождение осталось. Значит перекачка не помогает, и
    # следующие шесть часов трогать этот протокол незачем.
    state = (
        db_session.query(ProtocolSyncState)
        .join(Event, Event.id == ProtocolSyncState.event_id)
        .filter(Event.external_event_key == mismatched)
        .one()
    )
    state.last_protocol_fetched_at = NOW
    state.last_protocol_check_at = NOW
    db_session.flush()

    assert mismatched not in priority_keys(6)


def test_volunteers_mismatch_is_a_priority_reason() -> None:
    """Сводка обещает 13 волонтёров, у нас 12 — протокол надо перечитать.

    Проверено на живых протоколах 06.09.2026 (Черкизовский 05.09, Кудрово
    29.08, Тушино 22.08, Владимир 11.07): расхождение по волонтёрам — всегда
    реальный пропуск или лишняя строка, и перекачка его лечит
    (replace_event_volunteer_results и добавляет, и удаляет). Раньше это
    выглядело «шумом на ±1» из-за count(distinct participant_id), который
    молча терял волонтёров без профиля.
    """
    reason = _classify_reconcile_reason(
        _summary(volunteers_count=13),
        _state(),
        38,
        check_cutoff=NOW,
        fastest_stored_sec=1315,
        volunteer_people=12,
    )
    assert reason is ReconcileReason.volunteers_mismatch
    assert reason in PRIORITY_REASONS


def test_volunteers_match_leaves_the_protocol_alone() -> None:
    reason = _classify_reconcile_reason(
        _summary(volunteers_count=12),
        _state(),
        38,
        check_cutoff=NOW,
        fastest_stored_sec=1315,
        volunteer_people=12,
    )
    assert reason is ReconcileReason.check_due


def test_volunteers_unknown_in_summary_is_not_a_mismatch() -> None:
    """NULL в сводке — «не знаем», а не «ноль волонтёров»."""
    reason = _classify_reconcile_reason(
        _summary(volunteers_count=None),
        _state(),
        38,
        check_cutoff=NOW,
        fastest_stored_sec=1315,
        volunteer_people=12,
    )
    assert reason is ReconcileReason.check_due
