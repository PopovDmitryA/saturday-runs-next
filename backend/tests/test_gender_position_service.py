"""Место среди своего пола: parkrun считается только на русских площадках."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    RunResult,
)
from app.services.gender_position_service import recalculate_event_gender_positions


def _parkrun_platform(db_session: Session) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        platform = Platform(code="parkrun", name="parkrun")
        db_session.add(platform)
        db_session.flush()
    return platform


def _seed_parkrun_event(
    db_session: Session,
    *,
    catalogued: bool,
    finishers: list[tuple[int, str]],
    gender_position: int | None = None,
) -> Event:
    """Событие parkrun с финишёрами (место, категория участника вида «SW30-34»)."""
    suffix = str(uuid4().int % 1_000_000)
    platform = _parkrun_platform(db_session)
    location = Location(
        platform_id=platform.id,
        external_key=f"parkrun-gp-{suffix}",
        name="Parkrun GP Park",
        country="United Kingdom",
    )
    db_session.add(location)
    db_session.flush()

    if catalogued:
        catalog = LocationCatalog(
            canonical_name=f"Parkrun GP Park {suffix}",
            active_platform="five_verst",
            is_closed=False,
        )
        db_session.add(catalog)
        db_session.flush()
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=platform.id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"parkrun-gp-event-{suffix}",
        event_date=date(2019, 6, 1),
        event_number=100,
        title="Parkrun GP Event",
        finishers_count=len(finishers),
        runners_count=len(finishers),
    )
    db_session.add(event)
    db_session.flush()

    for index, (position, age_category) in enumerate(finishers):
        participant = Participant(
            platform_id=platform.id,
            external_user_id=f"parkrun-gp-user-{suffix}-{index}",
            display_name=f"GP Tester {index}",
            profile_url=f"https://www.parkrun.com/parkrunner/{suffix}{index}/",
            age_category=age_category,
        )
        db_session.add(participant)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"parkrun-gp-result-{suffix}-{index}",
                position=position,
                gender_position=gender_position,
                finish_time_sec=20 * 60 + position,
                finish_time_display="00:20:00",
                status="finished",
            )
        )
    db_session.flush()
    return event


def _gender_positions(db_session: Session, event: Event) -> list[int | None]:
    rows = (
        db_session.query(RunResult.position, RunResult.gender_position)
        .filter(RunResult.event_id == event.id)
        .order_by(RunResult.position)
        .all()
    )
    return [row.gender_position for row in rows]


def test_foreign_parkrun_has_no_gender_position(db_session: Session) -> None:
    """Протокола зарубежной площадки у нас нет — места по полу быть не может.

    Раньше строка из профиля («поле» из одного человека) давала gender_position=1,
    и любая женская пробежка за границей попадала в «Победы среди женщин».
    """
    event = _seed_parkrun_event(
        db_session, catalogued=False, finishers=[(37, "SW30-34")], gender_position=1
    )

    recalculate_event_gender_positions(db_session, event.id, "parkrun")

    assert _gender_positions(db_session, event) == [None]


def test_russian_parkrun_keeps_gender_position(db_session: Session) -> None:
    """Русский parkrun собран протоколами целиком — место по полу считается."""
    event = _seed_parkrun_event(
        db_session,
        catalogued=True,
        finishers=[(1, "SM30-34"), (2, "SW25-29"), (3, "VW45-49")],
    )

    recalculate_event_gender_positions(db_session, event.id, "parkrun")

    assert _gender_positions(db_session, event) == [1, 1, 2]
