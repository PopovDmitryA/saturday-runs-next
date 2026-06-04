from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Platform
from app.platform_adapters.canonical import CanonicalLocation
from app.sync import upsert


@pytest.fixture
def platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def test_upsert_event_finds_sibling_location_by_name(db_session: Session, platform: Platform) -> None:
    primary, _ = upsert.upsert_location(
        db_session,
        platform,
        CanonicalLocation(
            external_key="penza",
            name="Пенза",
            country="Россия",
            source_url="https://s95.ru/events/penza",
        ),
    )
    alias, _ = upsert.upsert_location(
        db_session,
        platform,
        CanonicalLocation(
            external_key="penza_alias",
            name="Пенза",
            country="Россия",
            source_url="https://s95.ru/events/penza",
        ),
    )
    existing = upsert.upsert_event_for_profile(
        db_session,
        platform,
        primary,
        external_event_key="penza:94:2026-02-07",
        event_date=date(2026, 2, 7),
        event_number=94,
        location_name="Пенза",
        location_slug="penza",
        source_url="https://s95.ru/events/penza",
    )
    db_session.flush()

    merged = upsert.upsert_event_for_profile(
        db_session,
        platform,
        alias,
        external_event_key="2026-02-07:penza",
        event_date=date(2026, 2, 7),
        event_number=94,
        location_name="Пенза",
        location_slug="penza_alias",
        source_url="https://s95.ru/events/penza",
    )
    db_session.flush()

    assert merged.id == existing.id
    count = (
        db_session.query(Event)
        .filter(
            Event.platform_id == platform.id,
            Event.event_date == date(2026, 2, 7),
            Event.location_id.in_([primary.id, alias.id]),
        )
        .count()
    )
    assert count == 1


def test_upsert_event_for_profile_merges_conflicting_external_key(
    db_session: Session,
    platform: Platform,
) -> None:
    location, _ = upsert.upsert_location(
        db_session,
        platform,
        CanonicalLocation(
            external_key="penza",
            name="Пенза",
            country="Россия",
            source_url="https://s95.ru/events/penza",
        ),
    )
    existing = upsert.upsert_event_for_profile(
        db_session,
        platform,
        location,
        external_event_key="penza:94:2026-02-07",
        event_date=date(2026, 2, 7),
        event_number=94,
        location_name="Пенза",
        location_slug="penza",
        source_url="https://s95.ru/events/penza",
    )
    db_session.flush()

    merged = upsert.upsert_event_for_profile(
        db_session,
        platform,
        location,
        external_event_key="2026-02-07:penza",
        event_date=date(2026, 2, 7),
        event_number=94,
        location_name="Пенза",
        location_slug="penza",
        source_url="https://s95.ru/events/penza",
    )
    db_session.flush()

    assert merged.id == existing.id
    assert merged.external_event_key == "2026-02-07:penza"
