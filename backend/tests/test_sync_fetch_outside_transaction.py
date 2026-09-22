"""Сетевые вызовы синков идут без открытой транзакции базы.

На проде `idle_in_transaction_session_timeout=60s`: если поход на 5verst.ru,
s95.ru или в MSSQL RunPark идёт внутри открытой (тем более грязной) транзакции,
Postgres рвёт соединение, ошибка всплывает первым запросом ПОСЛЕ фетча, а
незакоммиченные правки теряются. За 60 дней так закончились 38 прогонов
(находки SYNC-5V-04 и SYNC-OTHER-05, 21.09.2026).

Фейковые фетчи здесь проверяют состояние сессии в момент вызова: транзакция
должна быть закрыта (`release_before_fetch` / `commit_step` перед сетью).
В тестовой сессии (savepoint-режим conftest) `in_transaction()` ведёт себя как
в проде: False сразу после commit, True после любого SELECT или правки атрибута.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    RunparkLocationMapping,
    RunResult,
    User,
)
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalParticipant,
    CanonicalRunResult,
)
from app.platform_adapters.five_verst.bulk_parser import (
    LocationRegistryStatus,
    ParsedEventsPage,
    ParsedRegistryEntry,
)
from app.s95.api_client import S95ApiActivityRef


def _guarded(db: Session, fn, calls: list[str], label: str):
    """Обёртка над фейковым фетчем: падает, если сеть вызвана в открытой транзакции."""

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


# ----------------------------------------------------------------------------
# 5 вёрст
# ----------------------------------------------------------------------------


def _entry(slug: str, name: str, status: LocationRegistryStatus) -> ParsedRegistryEntry:
    return ParsedRegistryEntry(
        slug=slug,
        name=name,
        source_url=f"https://5verst.ru/{slug}/",
        city_group="Test City",
        status=status,
        cancel_reason=None,
    )


def test_five_verst_registry_fetches_coordinates_after_meta_is_committed(db_session: Session) -> None:
    """Правки меты (пауза, имя) коммитятся ДО похода за координатами."""
    from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry

    platform = _platform(db_session, "five_verst")
    slug = f"txguard-{uuid4().hex[:8]}"
    db_session.add(
        Location(
            platform_id=platform.id,
            external_key=slug,
            name="Old Name",
            is_paused=False,
            source_url=f"https://5verst.ru/{slug}/",
        )
    )
    db_session.commit()

    page = ParsedEventsPage(entries=[_entry(slug, "New Name", LocationRegistryStatus.paused)], saturday_cancellations=[])
    canonical = CanonicalLocation(
        external_key=slug,
        name="New Name",
        latitude=55.1,
        longitude=37.1,
        source_url=f"https://5verst.ru/{slug}/",
    )
    calls: list[str] = []
    with (
        patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")),
        patch(
            "app.sync.five_verst_locations.bulk_parser.fetch_location",
            side_effect=_guarded(db_session, lambda _slug: (canonical, "<html></html>"), calls, "fetch_location"),
        ),
        # Обратный геокодинг — тоже сеть (Nominatim), в тесте не нужен.
        patch("app.sync.five_verst_locations._enrich_location_geo", side_effect=lambda loc: loc),
        patch("app.sync.five_verst_locations._backfill_geo_for_row", return_value=False),
    ):
        result = sync_locations_registry(
            db_session, LocationRegistrySyncOptions(fetch_missing_coordinates=True, detect_duplicates=False)
        )

    assert result.errors == []
    assert calls == ["fetch_location"]
    assert result.coords_fetched == 1
    row = db_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
    assert row.is_paused is True
    assert row.name == "New Name"
    assert row.latitude == 55.1


def test_five_verst_registry_new_location_fetches_outside_transaction(db_session: Session) -> None:
    """Новая локация: страница локации — после release, проверка на дубль — после коммита локации."""
    from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry

    platform = _platform(db_session, "five_verst")
    slug = f"txnew-{uuid4().hex[:8]}"
    page = ParsedEventsPage(entries=[_entry(slug, "Brand New Park", LocationRegistryStatus.active)], saturday_cancellations=[])
    canonical = CanonicalLocation(
        external_key=slug,
        name="Brand New Park",
        latitude=55.2,
        longitude=37.2,
        source_url=f"https://5verst.ru/{slug}/",
    )
    calls: list[str] = []
    with (
        patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")),
        patch(
            "app.sync.five_verst_locations.bulk_parser.fetch_location",
            side_effect=_guarded(db_session, lambda _slug: (canonical, "<html></html>"), calls, "fetch_location"),
        ),
        patch(
            "app.sync.five_verst_locations.bulk_parser.fetch_event_summaries",
            side_effect=_guarded(db_session, lambda *a, **kw: ([], "<html></html>"), calls, "fetch_event_summaries"),
        ),
        patch("app.sync.five_verst_locations._enrich_location_geo", side_effect=lambda loc: loc),
    ):
        result = sync_locations_registry(db_session, LocationRegistrySyncOptions(detect_duplicates=True))

    assert result.errors == []
    assert calls == ["fetch_location", "fetch_event_summaries"]
    assert result.locations_created == 1
    assert (
        db_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
        is not None
    )


def test_five_verst_profile_protocol_fetches_outside_transaction(db_session: Session) -> None:
    """Протокол по запросу профиля: локация, саммари и сам протокол — три похода в сеть, все без транзакции."""
    from app.sync.profile_protocol_queue import fetch_five_verst_protocol_for_profile

    platform = _platform(db_session, "five_verst")
    slug = f"txprof-{uuid4().hex[:8]}"
    event_date = date(2099, 3, 7)
    canonical_location = CanonicalLocation(
        external_key=slug,
        name="Profile Park",
        source_url=f"https://5verst.ru/{slug}/",
    )
    summary = CanonicalEventSummary(
        external_event_key=f"{slug}:1:{event_date.isoformat()}",
        event_date=event_date,
        event_number=1,
        location_external_key=slug,
        location_name="Profile Park",
        finishers_count=1,
        volunteers_count=0,
        summary_hash="h1",
        source_url=f"https://5verst.ru/{slug}/results/{event_date.strftime('%d.%m.%Y')}/",
    )
    runner_id = str(uuid4().int % 10_000_000)
    run_results = [
        CanonicalRunResult(
            external_result_key=f"{slug}:{event_date.isoformat()}:{runner_id}",
            event_date=event_date,
            external_user_id=runner_id,
            participant_name="Бегун Тестовый",
            position=1,
            finish_time_sec=1500,
            finish_time_display="25:00",
            location_external_key=slug,
            location_name="Profile Park",
            event_number=1,
        )
    ]
    calls: list[str] = []
    with (
        patch(
            "app.platform_adapters.five_verst.bulk_parser.fetch_location",
            side_effect=_guarded(db_session, lambda _slug: (canonical_location, "<html></html>"), calls, "fetch_location"),
        ),
        patch(
            "app.platform_adapters.five_verst.bulk_parser.fetch_event_summaries",
            side_effect=_guarded(db_session, lambda *a, **kw: ([summary], "<html></html>"), calls, "fetch_event_summaries"),
        ),
        patch(
            "app.platform_adapters.five_verst.bulk_parser.fetch_event_protocol",
            side_effect=_guarded(db_session, lambda *a, **kw: (run_results, [], "<html></html>"), calls, "fetch_event_protocol"),
        ),
    ):
        fetch_five_verst_protocol_for_profile(
            db_session,
            location_slug=slug,
            event_date=event_date,
            event_number=1,
            location_name="Profile Park",
        )

    assert calls == ["fetch_location", "fetch_event_summaries", "fetch_event_protocol"]
    event = (
        db_session.query(Event)
        .filter(Event.platform_id == platform.id, Event.external_event_key == summary.external_event_key)
        .one()
    )
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).count() == 1


def test_five_verst_user_sync_fetches_outside_transaction(db_session: Session) -> None:
    """Пользовательский синк 5 вёрст: страница userstats — после отпускания транзакции."""
    from app.sync.user_sync import sync_platform_link

    platform = _platform(db_session, "five_verst")
    external_id = str(uuid4().int % 1_000_000_000)
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        external_user_id=external_id,
        external_url=f"https://5verst.ru/userstats/{external_id}/",
        sync_status=PlatformLinkSyncStatus.syncing,
    )
    db_session.add(link)
    db_session.commit()
    # Как в run_user_sync: после коммита статуса первое обращение к строке в
    # проде открывает транзакцию — здесь её открываем явно.
    db_session.refresh(link)
    assert db_session.in_transaction()

    profile = CanonicalParticipant(
        external_user_id=external_id,
        display_name="Тестовый Бегун",
        profile_url=f"https://5verst.ru/userstats/{external_id}/",
        total_runs=0,
    )
    calls: list[str] = []
    with (
        patch(
            "app.sync.user_sync.fetch_userstats_html",
            side_effect=_guarded(db_session, lambda _url: "<html></html>", calls, "fetch_userstats_html"),
        ),
        patch("app.sync.user_sync.parse_userstats_html", return_value=profile),
        patch("app.sync.user_sync.parse_userstats_runs_html", return_value=[]),
        patch("app.sync.user_sync.parse_userstats_volunteering_html", return_value=[]),
    ):
        result = sync_platform_link(db_session, link, platform)

    assert calls == ["fetch_userstats_html"]
    assert result["platform_code"] == "five_verst"
    assert link.sync_status == PlatformLinkSyncStatus.ok


# ----------------------------------------------------------------------------
# S95
# ----------------------------------------------------------------------------

S95_ACTIVITY_JSON = {
    "date": "26.10.2024",
    "event": {"name": "Пенза", "code_name": "penza", "town": "Пенза"},
    "results": [
        {"total_time": "24:27", "position": 1, "athlete": {"id": 15512, "name": "Наталия МАШТАКОВА", "gender": "female"}},
    ],
    "volunteers": [],
}


def test_s95_profile_protocol_fetches_outside_transaction(db_session: Session) -> None:
    """Протокол S95 по запросу профиля: список стартов и JSON протокола — оба после коммита."""
    from app.sync.profile_protocol_queue import fetch_s95_protocol_for_profile

    platform = _platform(db_session, "s95")
    slug = f"txs95-{uuid4().hex[:8]}"
    event_date = date(2024, 10, 26)
    ref = S95ApiActivityRef(date=event_date.isoformat(), url="https://s95.ru/activities/1855.json")
    calls: list[str] = []
    with (
        patch(
            "app.sync.s95_protocol_lookup.fetch_event_activities",
            side_effect=_guarded(db_session, lambda _url: [ref], calls, "fetch_event_activities"),
        ),
        patch(
            "app.sync.s95_protocol_api.fetch_activity",
            side_effect=_guarded(db_session, lambda _url: S95_ACTIVITY_JSON, calls, "fetch_activity"),
        ),
    ):
        fetch_s95_protocol_for_profile(
            db_session,
            location_slug=slug,
            event_date=event_date,
            location_name="Пенза",
        )

    assert calls == ["fetch_event_activities", "fetch_activity"]
    event = (
        db_session.query(Event)
        .filter(Event.platform_id == platform.id, Event.external_event_key == f"{slug}:{event_date.isoformat()}")
        .one()
    )
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).count() == 1


def test_s95_user_sync_fetches_outside_transaction(db_session: Session) -> None:
    """Пользовательский синк S95: страница атлета — после отпускания транзакции."""
    from app.sync.s95_user_sync import sync_s95_platform_link

    platform = _platform(db_session, "s95")
    external_id = str(uuid4().int % 1_000_000_000)
    user = User(telegram_id=int(uuid4().int % 10_000_000_000), consent_accepted=True)
    db_session.add(user)
    db_session.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        external_user_id=external_id,
        external_url=f"https://s95.ru/athletes/{external_id}/",
        sync_status=PlatformLinkSyncStatus.syncing,
    )
    db_session.add(link)
    db_session.commit()
    db_session.refresh(link)
    assert db_session.in_transaction()

    profile = CanonicalParticipant(
        external_user_id=external_id,
        display_name="Runner",
        profile_url=f"https://s95.ru/athletes/{external_id}/",
        total_runs=0,
    )
    calls: list[str] = []
    with (
        patch(
            "app.sync.s95_user_sync.s95_parser.fetch_athlete_activity",
            side_effect=_guarded(db_session, lambda *a, **kw: (profile, [], []), calls, "fetch_athlete_activity"),
        ),
        patch(
            "app.sync.parkrun_participant_discovery.enqueue_parkrun_discovery_from_barcode",
            return_value=MagicMock(found=False, participant_id=None, runs_imported=0, volunteering_imported=0),
        ),
    ):
        result = sync_s95_platform_link(db_session, link, platform)

    assert calls == ["fetch_athlete_activity"]
    assert result["platform_code"] == "s95"
    assert link.sync_status == PlatformLinkSyncStatus.ok


# ----------------------------------------------------------------------------
# RunPark
# ----------------------------------------------------------------------------

RP_EVENT_ID = "AAAA9999-2222-3333-4444-555566667777"
RP_LOCATION_ID = "BBBB9999-2222-3333-4444-555566667777"
RP_RESULT_ID = "CCCC9999-2222-3333-4444-555566667777"
RP_STALE_RESULT_ID = "EEEE9999-2222-3333-4444-555566667777"
RP_PARTICIPANT_ID = "DDDD9999-2222-3333-4444-555566667777"
RP_BARCODE = "A9990001"


def _runpark_rows(finish_time_sec: int) -> dict[str, list[dict]]:
    event_date = datetime(2026, 8, 1, 9, 0)
    return {
        "vw_events": [
            {
                "event_id": RP_EVENT_ID,
                "location_id": RP_LOCATION_ID,
                "event_date": event_date,
                "event_number": 12,
                "is_test_event": False,
                "finishers_count": 1,
            }
        ],
        "vw_run_results": [
            {
                "result_id": RP_RESULT_ID,
                "event_id": RP_EVENT_ID,
                "event_date": event_date,
                "participant_id": RP_PARTICIPANT_ID,
                "participant_name": "Тестовый Бегун",
                "barcode_id": RP_BARCODE,
                "position": 1,
                "finish_time_sec": finish_time_sec,
                "finish_time_display": "00:20:00",
                "age_category": "М30-34",
                "status": "finished",
                "is_pr": False,
            }
        ],
        "vw_volunteer_results": [],
    }


def test_runpark_batch_queries_mssql_outside_transaction(db_session: Session) -> None:
    """Батч RunPark: вьюхи события и сверка штрихкода — все запросы к MSSQL без открытой транзакции.

    Сверка штрихкода (reconcile_barcode_identity) раньше шла в MSSQL уже
    после записи события — с грязной сессией.
    """
    from app.sync.runpark_global_sync import sync_runpark_batch

    platform = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if platform is None:
        platform = Platform(code="runpark", name="RunPark", base_url="https://runpark.ru")
        db_session.add(platform)
        db_session.flush()
    location = Location(platform_id=platform.id, external_key=f"runpark-txguard-{uuid4().hex[:8]}", name="Тестовый парк")
    db_session.add(location)
    db_session.flush()
    db_session.add(
        RunparkLocationMapping(
            runpark_location_id=RP_LOCATION_ID,
            runpark_name="Тестовый парк",
            decision="load_history",
            show_on_map=True,
            runpark_location_row_id=location.id,
            source_batch="test",
        )
    )
    db_session.commit()

    calls: list[str] = []

    def _fake_query(rows: dict[str, list[dict]]):
        def query(sql: str, params: tuple = ()) -> list[dict]:
            if "WHERE barcode_id" in sql:
                return [{"result_id": RP_STALE_RESULT_ID, "participant_id": RP_PARTICIPANT_ID}]
            for view, payload in rows.items():
                if view in sql:
                    return payload
            raise AssertionError(f"Unexpected runpark query: {sql}")

        return _guarded(db_session, query, calls, "runpark_query")

    # Первый прогон: событие и результат появляются, штрихкодной пустышки ещё нет.
    with patch("app.sync.runpark_global_sync.runpark_query", side_effect=_fake_query(_runpark_rows(1200))):
        first = sync_runpark_batch(db_session, date(2026, 7, 25))
    assert first.errors == []
    assert first.events_upserted == 1
    assert calls == ["runpark_query"] * 3  # vw_events + run rows + vol rows

    # Пустышка «barcode:…» с забегом: второй прогон с изменённым протоколом
    # должен сверить штрихкод в MSSQL и перевесить строку на аккаунт.
    event = db_session.query(Event).filter(Event.external_event_key == RP_EVENT_ID).one()
    stale = Participant(platform_id=platform.id, external_user_id=f"barcode:{RP_BARCODE}", display_name="По штрихкоду")
    db_session.add(stale)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=stale.id,
            external_result_key=RP_STALE_RESULT_ID,
            position=2,
            finish_time_sec=1300,
        )
    )
    db_session.commit()

    calls.clear()
    with patch("app.sync.runpark_global_sync.runpark_query", side_effect=_fake_query(_runpark_rows(1250))):
        second = sync_runpark_batch(db_session, date(2026, 7, 25))
    assert second.errors == []
    assert second.events_upserted == 1
    assert second.barcode_rows_reassigned == 1
    assert calls == ["runpark_query"] * 4  # + сверка штрихкода


# ----------------------------------------------------------------------------
# parkrun
# ----------------------------------------------------------------------------


def test_parkrun_import_fetches_outside_transaction(db_session: Session) -> None:
    """Импорт атлета parkrun: проверка свежести открыла транзакцию — перед браузерным фетчем она отпущена."""
    from app.sync.parkrun_participant_import import import_parkrun_participant_activity

    platform = _platform(db_session, "parkrun")
    athlete_id = str(uuid4().int % 100_000_000)
    profile = CanonicalParticipant(
        external_user_id=athlete_id,
        display_name="Discovered Runner",
        profile_url=f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/",
        total_runs=0,
        total_volunteering=0,
        barcode_id=f"A{athlete_id}",
    )
    calls: list[str] = []
    with patch(
        "app.sync.parkrun_participant_import.parkrun_parser.fetch_athlete_by_id",
        side_effect=_guarded(db_session, lambda _id: (profile, [], [], {}), calls, "fetch_athlete_by_id"),
    ):
        result = import_parkrun_participant_activity(db_session, platform, athlete_id, max_age_seconds=3600)

    assert calls == ["fetch_athlete_by_id"]
    assert result.participant.external_user_id == athlete_id
