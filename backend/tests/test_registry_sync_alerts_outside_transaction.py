"""Реестры локаций и клубы: геокодер и тревоги админу — без открытой транзакции.

Продолжение test_sync_fetch_outside_transaction: там фетчи страниц, здесь
остальная сеть этих синков — Nominatim (`apply_reverse_geocode_to_location`) и
`notify_admin` (Telegram через прокси, при сбое ВК, до 30 с на каждый). На проде
`idle_in_transaction_session_timeout=60s`, и грязная транзакция на время такого
вызова теряла заявку на слияние, состав клуба или смену статуса.

Сессия здесь с `expire_on_commit=True`, как на проде: протухшее после коммита
поле читается новым SELECT, и сетевой вызов, читающий ORM-объект после
коммита, снова оказался бы внутри транзакции — conftest этого не ловит.

Там же — отмены: в рассылку попадает только закоммиченная смена статуса.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models import (
    Club,
    ClubMember,
    EventSummary,
    Location,
    LocationMergeRequest,
    Participant,
    Platform,
)
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation
from app.platform_adapters.five_verst.bulk_parser import (
    LocationRegistryStatus,
    ParsedEventsPage,
    ParsedRegistryEntry,
)
from app.platform_adapters.five_verst.clubs_parser import ParsedClubDetail, ParsedClubMember
from app.s95.api_client import S95ApiLocation
from app.sync.s95_location_status import S95LocationStatus

MOSCOW = {"country": "Россия", "region": "Москва", "city": "Москва"}


@pytest.fixture
def prod_session() -> Generator[Session, None, None]:
    """Как db_session из conftest, но поля протухают после коммита — как на проде."""
    try:
        connection = get_engine().connect()
    except Exception:
        pytest.skip("Database not available")
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=True, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _guarded(db: Session, fn, calls: list[str], label: str):
    """Обёртка над фейковым сетевым вызовом: падает, если транзакция открыта."""

    def wrapper(*args, **kwargs):
        assert not db.in_transaction(), f"{label}: сетевой вызов при открытой транзакции базы"
        calls.append(label)
        return fn(*args, **kwargs)

    return wrapper


def _platform(db: Session, code: str) -> Platform:
    row = db.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        pytest.skip(f"{code} platform not seeded")
    return row


def _five_verst_entry(slug: str, name: str, status: LocationRegistryStatus) -> ParsedRegistryEntry:
    return ParsedRegistryEntry(
        slug=slug,
        name=name,
        source_url=f"https://5verst.ru/{slug}/",
        city_group="Test City",
        status=status,
        cancel_reason=None,
    )


def _s95_entry(slug: str, name: str, *, active: bool, lat: float, lon: float) -> S95ApiLocation:
    return S95ApiLocation(
        domain="https://s95.ru",
        slug=slug,
        name=name,
        town="Москва",
        place="Парк",
        active=active,
        latitude=lat,
        longitude=lon,
    )


# ----------------------------------------------------------------------------
# 5 вёрст: реестр локаций
# ----------------------------------------------------------------------------


def test_five_verst_registry_geocodes_after_commit(prod_session: Session) -> None:
    """Строке без региона геокодер зовётся после коммита правок записи (отмены)."""
    from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry

    platform = _platform(prod_session, "five_verst")
    slug = f"txgeo-{uuid4().hex[:8]}"
    prod_session.add(
        Location(
            platform_id=platform.id,
            external_key=slug,
            name="Geo Park",
            latitude=55.75,
            longitude=37.62,
            is_official_map=True,
            source_url=f"https://5verst.ru/{slug}/",
        )
    )
    prod_session.commit()

    page = ParsedEventsPage(
        entries=[_five_verst_entry(slug, "Geo Park", LocationRegistryStatus.cancelled)],
        saturday_cancellations=[],
    )
    calls: list[str] = []
    with (
        patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")),
        patch(
            "app.services.location_geo_service.lookup_address",
            side_effect=_guarded(prod_session, lambda *_a: MOSCOW, calls, "lookup_address"),
        ),
        patch("app.sync.five_verst_locations.notify_cancellation_changes") as notify,
        patch("app.sync.five_verst_locations.flush_location_page_caches"),
    ):
        result = sync_locations_registry(
            prod_session, LocationRegistrySyncOptions(fetch_missing_coordinates=False, detect_duplicates=False)
        )

    assert result.errors == []
    assert calls == ["lookup_address"]
    assert result.regions_backfilled == 1
    assert [item.slug for item in result.cancellation_changes] == [slug]
    assert notify.call_count == 1
    row = prod_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
    assert row.is_cancelled is True
    assert row.region == "Москва"
    assert row.country == "Россия"
    # Город группы реестра главнее геокодера — его геокодер не перебивает.
    assert row.city == "Test City"


def test_five_verst_registry_merge_alert_after_commit(prod_session: Session) -> None:
    """Заявка на слияние коммитится до тревоги, пометка notified_at — после неё."""
    from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry

    platform = _platform(prod_session, "five_verst")
    old_slug = f"txold-{uuid4().hex[:8]}"
    new_slug = f"txnew-{uuid4().hex[:8]}"
    existing = Location(
        platform_id=platform.id,
        external_key=old_slug,
        name="Old Slug Park",
        latitude=55.0,
        longitude=37.0,
        is_official_map=True,
    )
    prod_session.add(existing)
    prod_session.flush()
    overlap_dates = [date(2099, 1, 6), date(2099, 1, 13), date(2099, 1, 20), date(2099, 1, 27), date(2099, 2, 3)]
    for event_date in overlap_dates:
        prod_session.add(
            EventSummary(
                platform_id=platform.id,
                location_id=existing.id,
                external_event_key=f"{old_slug}:1:{event_date.isoformat()}",
                event_date=event_date,
                event_number=1,
                summary_hash="abc",
            )
        )
    prod_session.commit()

    page = ParsedEventsPage(
        entries=[_five_verst_entry(new_slug, "New Slug Park", LocationRegistryStatus.active)],
        saturday_cancellations=[],
    )
    canonical = CanonicalLocation(
        external_key=new_slug,
        name="New Slug Park",
        latitude=55.1,
        longitude=37.1,
        source_url=f"https://5verst.ru/{new_slug}/",
        **MOSCOW,
    )
    summaries = [
        CanonicalEventSummary(
            external_event_key=f"{new_slug}:1:{event_date.isoformat()}",
            event_date=event_date,
            event_number=1,
            location_external_key=new_slug,
            location_name="New Slug Park",
            finishers_count=10,
            volunteers_count=5,
            summary_hash=f"hash-{event_date.isoformat()}",
        )
        for event_date in overlap_dates
    ]
    calls: list[str] = []
    with (
        patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")),
        patch(
            "app.sync.five_verst_locations.bulk_parser.fetch_location",
            side_effect=_guarded(prod_session, lambda _slug: (canonical, "<html></html>"), calls, "fetch_location"),
        ),
        patch(
            "app.sync.five_verst_locations.bulk_parser.fetch_event_summaries",
            side_effect=_guarded(prod_session, lambda *a, **kw: (summaries, "<html></html>"), calls, "fetch_event_summaries"),
        ),
        patch("app.sync.five_verst_locations._enrich_location_geo", side_effect=lambda loc: loc),
        patch(
            "app.sync.five_verst_locations.notify_admin",
            side_effect=_guarded(prod_session, lambda _text: True, calls, "notify_admin"),
        ),
    ):
        result = sync_locations_registry(prod_session, LocationRegistrySyncOptions())

    assert result.errors == []
    assert calls == ["fetch_location", "fetch_event_summaries", "notify_admin"]
    assert result.merge_requests_created == 1
    assert result.merge_notifications_sent == 1
    merge = prod_session.query(LocationMergeRequest).filter(LocationMergeRequest.candidate_slug == new_slug).one()
    assert merge.matched_slug == old_slug
    assert merge.notified_at is not None


def test_five_verst_registry_skips_cancellation_when_commit_fails(db_session: Session) -> None:
    """Коммит записи упал — отмена откатилась, и рассылки о ней нет."""
    from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry

    platform = _platform(db_session, "five_verst")
    slug = f"txfail-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Fail Park",
        latitude=55.75,
        longitude=37.62,
        is_official_map=True,
        is_cancelled=False,
        source_url=f"https://5verst.ru/{slug}/",
        **MOSCOW,
    )
    db_session.add(location)
    db_session.commit()

    page = ParsedEventsPage(
        entries=[_five_verst_entry(slug, "Fail Park", LocationRegistryStatus.cancelled)],
        saturday_cancellations=[],
    )
    with (
        patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")),
        patch("app.sync.five_verst_locations.commit_step", side_effect=RuntimeError("commit failed")),
        patch("app.sync.five_verst_locations.notify_cancellation_changes") as notify,
    ):
        result = sync_locations_registry(
            db_session, LocationRegistrySyncOptions(fetch_missing_coordinates=False, detect_duplicates=False)
        )

    assert result.errors == [f"{slug}: commit failed"]
    assert result.cancellation_changes == []
    assert result.cancel_status_changed == 0
    assert result.cancel_changed_locations == []
    assert notify.call_count == 0
    db_session.refresh(location)
    assert location.is_cancelled is False


# ----------------------------------------------------------------------------
# S95: реестр локаций
# ----------------------------------------------------------------------------


def test_s95_registry_geocodes_after_commit(prod_session: Session) -> None:
    """Строке без региона и города геокодер зовётся после коммита правок записи."""
    from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry

    platform = _platform(prod_session, "s95")
    slug = f"txs95geo-{uuid4().hex[:8]}"
    prod_session.add(
        Location(
            platform_id=platform.id,
            external_key=slug,
            name="Old Name",
            latitude=55.75,
            longitude=37.62,
            is_official_map=True,
            source_url=f"https://s95.ru/events/{slug}",
        )
    )
    prod_session.commit()

    calls: list[str] = []
    with (
        patch(
            "app.sync.s95_locations_registry.fetch_all_locations",
            return_value=[_s95_entry(slug, "Новое имя", active=True, lat=55.75, lon=37.62)],
        ),
        patch(
            "app.services.location_geo_service.lookup_address",
            side_effect=_guarded(prod_session, lambda *_a: MOSCOW, calls, "lookup_address"),
        ),
    ):
        result = sync_s95_locations_registry(prod_session, S95LocationRegistrySyncOptions())

    assert result.errors == []
    assert calls == ["lookup_address"]
    assert result.regions_backfilled == 1
    row = prod_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
    assert row.name == "Новое имя"
    assert row.region == "Москва"
    assert row.city == "Москва"


def test_s95_registry_skips_cancellation_when_commit_fails(db_session: Session) -> None:
    """Коммит записи упал — отмена откатилась, и рассылки о ней нет."""
    from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry

    platform = _platform(db_session, "s95")
    slug = f"txs95fail-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Иваново",
        latitude=57.00297,
        longitude=40.976711,
        is_official_map=True,
        is_cancelled=False,
        source_url=f"https://s95.ru/events/{slug}",
        **MOSCOW,
    )
    db_session.add(location)
    db_session.commit()

    status = S95LocationStatus(is_paused=False, is_cancelled=True, cancel_reason="Отмена забега")
    with (
        patch(
            "app.sync.s95_locations_registry.fetch_all_locations",
            return_value=[_s95_entry(slug, "Иваново", active=False, lat=57.00297, lon=40.976711)],
        ),
        patch("app.sync.s95_locations_registry.resolve_s95_location_status", return_value=status),
        patch("app.sync.s95_locations_registry.commit_step", side_effect=RuntimeError("commit failed")),
        patch("app.sync.s95_locations_registry.notify_cancellation_changes") as notify,
    ):
        result = sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    assert result.errors == [f"{slug}: commit failed"]
    assert result.cancellation_changes == []
    assert result.cancel_status_changed == 0
    assert result.cancel_changed_locations == []
    assert notify.call_count == 0
    db_session.refresh(location)
    assert location.is_cancelled is False


# ----------------------------------------------------------------------------
# 5 вёрст: клубы
# ----------------------------------------------------------------------------


def test_five_verst_club_slug_alert_after_commit(prod_session: Session) -> None:
    """Тревога о смене slug клуба уходит после коммита состава, а не в грязной транзакции."""
    from app.sync.five_verst_clubs import sync_club_details_batch

    platform = _platform(prod_session, "five_verst")
    old_slug = f"txclubold-{uuid4().hex[:8]}"
    new_slug = f"txclubnew-{uuid4().hex[:8]}"
    old_club = Club(platform_id=platform.id, external_key=old_slug, name="Old Club", is_active=False)
    new_club = Club(platform_id=platform.id, external_key=new_slug, name="New Club", is_active=True, needs_detail_sync=True)
    prod_session.add_all([old_club, new_club])
    prod_session.flush()
    user_ids = [str(900_000_000 + uuid4().int % 90_000_000) for _ in range(3)]
    participants = [
        Participant(platform_id=platform.id, external_user_id=user_id, display_name=f"Бегун {index}")
        for index, user_id in enumerate(user_ids)
    ]
    prod_session.add_all(participants)
    prod_session.flush()
    now = datetime.now(timezone.utc)
    for participant in participants:
        prod_session.add(
            ClubMember(club_id=old_club.id, participant_id=participant.id, first_seen_at=now, last_seen_at=now)
        )
    # В dev-базе есть настоящие клубы: батч должен взять только тестовый.
    prod_session.query(Club).filter(Club.platform_id == platform.id, Club.external_key != new_slug).update(
        {"is_active": False}, synchronize_session=False
    )
    prod_session.commit()

    detail = ParsedClubDetail(
        slug=new_slug,
        title="New Club",
        description="Тестовый клуб",
        logo_url=None,
        external_links=[],
        members=[
            ParsedClubMember(
                userstats_id=user_id,
                display_name=f"Бегун {index}",
                profile_url=f"https://5verst.ru/userstats/{user_id}/",
            )
            for index, user_id in enumerate(user_ids)
        ],
        members_skipped=0,
    )
    calls: list[str] = []
    with (
        patch(
            "app.platform_adapters.five_verst.clubs_parser.fetch_club_detail",
            side_effect=_guarded(prod_session, lambda _slug: (detail, "<html/>"), calls, "fetch_club_detail"),
        ),
        patch(
            "app.sync.five_verst_clubs.notify_admin",
            side_effect=_guarded(prod_session, lambda _text: True, calls, "notify_admin"),
        ),
    ):
        result = sync_club_details_batch(prod_session, limit=1)

    assert result.errors == []
    assert calls == ["fetch_club_detail", "notify_admin"]
    assert result.slug_change_alerts == 1
    club = prod_session.query(Club).filter(Club.platform_id == platform.id, Club.external_key == new_slug).one()
    assert club.detail_synced_at is not None
    assert prod_session.query(ClubMember).filter(ClubMember.club_id == club.id).count() == 3
