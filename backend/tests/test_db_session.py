from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models import Event, Location, Platform


def test_db_session_rolls_back_after_test(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        return

    marker = f"rollback-probe-{uuid4().hex}"
    location = Location(
        platform_id=platform.id,
        external_key=marker,
        name="Rollback Probe",
        source_url=f"https://5verst.ru/{marker}/",
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=marker,
            event_date=date(2020, 1, 1),
            event_number=1,
            title="Rollback Probe",
        )
    )
    db_session.commit()

    assert db_session.query(Event).filter(Event.external_event_key == marker).one() is not None


def test_db_session_rolls_back_previous_test_data(db_session: Session) -> None:
    engine = get_engine()
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT COUNT(*) FROM events WHERE external_event_key LIKE 'rollback-probe-%'")
        ).scalar()
    assert count == 0
