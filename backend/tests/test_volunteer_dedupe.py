from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.models import Event, Location, Participant, VolunteerResult
from app.sync import upsert


def test_dedupe_participant_volunteer_results_keeps_protocol_row(
    db_session,
) -> None:
    platform = upsert.get_platform(db_session, "five_verst")
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=platform.id,
        external_key=f"druzhba-{suffix}",
        name="Дружба",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"user-{suffix}",
        display_name="Volunteer",
    )
    db_session.add(participant)
    db_session.flush()

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"2026-05-30:druzhba-{suffix}",
        event_date=date(2026, 5, 30),
        title="Дружба",
    )
    db_session.add(event)
    db_session.flush()

    db_session.add_all(
        [
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"user-{suffix}:2026-05-30:druzhba-{suffix}:svyazi_s_obschestvennostyu",
                role="Связи с общественностью",
            ),
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"druzhba-{suffix}:2026-05-30:vol:user-{suffix}:svyazi_s_obschestvennostyu",
                role="Связи с общественностью",
            ),
        ]
    )
    db_session.flush()

    deleted = upsert.dedupe_participant_volunteer_results(db_session, platform.id, participant.id)
    rows = (
        db_session.query(VolunteerResult)
        .filter(VolunteerResult.participant_id == participant.id)
        .all()
    )

    assert deleted == 1
    assert len(rows) == 1
    assert ":vol:" in rows[0].external_result_key
