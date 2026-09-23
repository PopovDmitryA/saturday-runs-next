"""Заглушки «неизвестного» на пропущенных местах русского parkrun."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, RunResult
from app.parkrun.protocol_gaps import PLACEHOLDER_STATUS, missing_positions, placeholder_key
from app.platform_adapters.canonical import CanonicalLocation, CanonicalRunResult
from app.sync import upsert


def test_missing_positions_fills_only_inside_the_protocol() -> None:
    # Хвост после последнего известного места не выдумываем.
    assert missing_positions([1, 2, 4, 7]) == [3, 5, 6]
    assert missing_positions([2, None]) == [1]
    assert missing_positions([1, 2, 3]) == []
    assert missing_positions([None]) == []


def test_real_finisher_from_profile_takes_the_placeholder_place(db_session: Session) -> None:
    platform = upsert.get_platform(db_session, "parkrun")
    slug = f"gaps-{uuid4().hex[:8]}"
    location, _ = upsert.upsert_location(db_session, platform, CanonicalLocation(external_key=slug, name="Дыры"))
    event_date = date(2021, 6, 26)
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{slug}:42",
        event_date=event_date,
        event_number=42,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=None,
            external_result_key=placeholder_key(slug, event_date, 3),
            position=3,
            status=PLACEHOLDER_STATUS,
        )
    )
    db_session.flush()

    upsert.upsert_run_results(
        db_session,
        event,
        platform,
        [
            CanonicalRunResult(
                external_result_key=f"parkrun:123456:{slug}:{event_date.isoformat()}:42",
                event_date=event_date,
                external_user_id="123456",
                participant_name="Иван ПЕТРОВ",
                position=3,
                finish_time_sec=1500,
                finish_time_display="00:25:00",
                location_external_key=slug,
                location_name="Дыры",
            )
        ],
        from_profile=True,
        recalculate_pr=False,
    )
    db_session.flush()

    rows = db_session.query(RunResult).filter(RunResult.event_id == event.id).all()
    assert len(rows) == 1
    assert rows[0].participant_id is not None
    assert rows[0].position == 3
