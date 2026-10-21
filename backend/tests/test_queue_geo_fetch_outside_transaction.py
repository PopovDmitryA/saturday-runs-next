"""Сеть без открытой транзакции: очередь профилей, каталог parkrun, геокодинг.

На проде `idle_in_transaction_session_timeout=60s`: поход в сеть внутри
открытой транзакции заканчивается тем, что Postgres рвёт соединение, а падает
первый запрос ПОСЛЕ фетча. Фейковые фетчи здесь проверяют, что в момент вызова
транзакция сессии закрыта (тот же приём, что в test_sync_fetch_outside_transaction).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import (
    Location,
    NearbyQueryLog,
    Participant,
    Platform,
    ProfileFetchPending,
    ProfileFetchPendingOperation,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
)
from app.platform_adapters.canonical import CanonicalLocation, CanonicalParticipant, ProfilePreview


def _guarded(db: Session, fn, calls: list[str], label: str):
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


def _external_id() -> str:
    return str(790_000_000 + uuid4().int % 9_000_000)


# ----------------------------------------------------------------------------
# Очередь профилей (profile_fetch_pending)
# ----------------------------------------------------------------------------


def _pending_row(db: Session, platform_code: str, profile_input: str, operation, external_user_id=None):
    row = ProfileFetchPending(
        platform_code=platform_code,
        profile_input=profile_input,
        external_user_id=external_user_id,
        operation=operation,
        reason=ProfileFetchPendingReason.error,
    )
    db.add(row)
    db.commit()
    return row


def test_pending_s95_activity_import_fetches_outside_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Импорт активности S95 из очереди: refresh строки и чтение платформы не
    держат транзакцию на время загрузки профиля."""
    import app.services.profile_fetch_pending_service as svc

    _platform(db_session, "s95")
    monkeypatch.setattr(svc, "is_platform_in_cooldown", lambda _code: False)
    external_id = _external_id()
    profile_url = f"https://s95.ru/athletes/{external_id}/"
    row = _pending_row(
        db_session, "s95", profile_url, ProfileFetchPendingOperation.activity_import, external_user_id=external_id
    )
    profile = CanonicalParticipant(
        external_user_id=external_id,
        display_name="Queue Runner",
        profile_url=profile_url,
        total_runs=0,
    )
    calls: list[str] = []
    with patch(
        "app.platform_adapters.s95.parser.fetch_athlete_activity",
        side_effect=_guarded(db_session, lambda *a, **kw: (profile, [], []), calls, "fetch_athlete_activity"),
    ):
        outcome = svc.process_pending_row(db_session, row)

    assert outcome == "done"
    assert calls == ["fetch_athlete_activity"]
    assert row.status == ProfileFetchPendingStatus.done
    assert row.external_user_id == external_id


def test_pending_profile_preview_fetches_outside_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Превью без владельца (_persist_profile_preview): фетч — вне транзакции."""
    import app.services.profile_fetch_pending_service as svc

    platform = _platform(db_session, "five_verst")
    monkeypatch.setattr(svc, "is_platform_in_cooldown", lambda _code: False)
    external_id = _external_id()
    profile_url = f"https://5verst.ru/userstats/{external_id}/"
    row = _pending_row(db_session, "five_verst", profile_url, ProfileFetchPendingOperation.profile_preview)
    preview = ProfilePreview(
        external_user_id=external_id,
        display_name="Preview Runner",
        profile_url=profile_url,
        platform_code="five_verst",
    )
    calls: list[str] = []
    with patch(
        "app.platform_adapters.five_verst.adapter.FiveVerstAdapter.fetch_profile_preview",
        side_effect=_guarded(db_session, lambda *a, **kw: preview, calls, "fetch_profile_preview"),
    ):
        outcome = svc.process_pending_row(db_session, row)

    assert outcome == "done"
    assert calls == ["fetch_profile_preview"]
    participant = (
        db_session.query(Participant)
        .filter(Participant.platform_id == platform.id, Participant.external_user_id == external_id)
        .one()
    )
    assert participant.display_name == "Preview Runner"


# ----------------------------------------------------------------------------
# Каталог parkrun
# ----------------------------------------------------------------------------


def test_parkrun_catalog_downloads_outside_transaction(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """id прогона берётся до коммита: при expire_on_commit=True (как на проде)
    чтение run.id после коммита открыло бы транзакцию прямо перед скачиванием."""
    import json

    from app.parkrun.event_slugs import COUNTRY_DOMAINS
    from app.services import parkrun_catalog_service as service

    connection = db_session.connection()
    db_session.execute(text("delete from parkrun_catalog_runs"))
    db_session.execute(text("delete from parkrun_events"))
    db_session.flush()
    sessions: list[Session] = []

    def factory() -> Session:
        session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=True)
        sessions.append(session)
        return session

    tag = uuid4().hex[:6]
    features = [
        {
            "id": 9_200_000 + i,
            "geometry": {"coordinates": [-1.0, 51.0]},
            "properties": {
                "eventname": f"guard{i}{tag}",
                "EventShortName": f"Guard {i} {tag}",
                "EventLongName": f"Guard {i} {tag} parkrun",
                "countrycode": 97,
                "seriesid": 1,
                "EventLocation": f"Guard {i}",
            },
        }
        for i in range(32)
    ]
    countries = {"0": {"url": None}, **{str(code): {"url": domain} for code, domain in COUNTRY_DOMAINS.items()}}
    data = json.dumps({"countries": countries, "events": {"type": "FeatureCollection", "features": features}}).encode()

    calls: list[str] = []

    def fetch() -> bytes:
        assert not sessions[0].in_transaction(), "каталог скачивается при открытой транзакции базы"
        calls.append("fetch")
        return data

    monkeypatch.setattr(service, "fetch_catalog_payload", fetch)
    result = service.run_catalog_sync(factory, heal=False, notify=False, now=datetime(2026, 10, 18, 6, 20, tzinfo=timezone.utc))

    assert calls == ["fetch"]
    assert result["status"] == "ok"


# ----------------------------------------------------------------------------
# Подпись белых пятен (Nominatim)
# ----------------------------------------------------------------------------


def test_nearby_label_place_geocodes_outside_transaction(db_session: Session) -> None:
    from app.services.nearby_query_log_service import label_place

    row = NearbyQueryLog(
        source="bot",
        cell_latitude=-48.85,
        cell_longitude=-123.4,
        nearest_identity_key=None,
        nearest_distance_km=900.0,
        within_radius=0,
        is_linked=False,
    )
    db_session.add(row)
    db_session.commit()
    row_id = row.id

    calls: list[str] = []
    with patch(
        "app.geo.reverse_geocode.lookup_address",
        side_effect=_guarded(
            db_session,
            lambda lat, lon: {"city": None, "region": None, "country": "Точка Немо"},
            calls,
            "lookup_address",
        ),
    ):
        assert label_place(db_session, row_id) == "Точка Немо"

    assert calls == ["lookup_address"]
    assert db_session.get(NearbyQueryLog, row_id).place_label == "Точка Немо"


# ----------------------------------------------------------------------------
# Ручной добор гео локаций (scripts/backfill_location_geo.py)
# ----------------------------------------------------------------------------


def test_backfill_location_geo_geocodes_outside_transaction(db_session: Session) -> None:
    """Каждая локация — свой коммит, Nominatim — без открытой транзакции."""
    from app.services.location_geo_service import backfill_location_geo

    platform = _platform(db_session, "five_verst")
    locations = [
        Location(
            platform_id=platform.id,
            external_key=f"geo-guard-{uuid4().hex[:8]}",
            name=f"000 Geo Guard {index}",
            latitude=55.75 + index / 100,
            longitude=37.62,
            is_official_map=True,
        )
        for index in range(2)
    ]
    db_session.add_all(locations)
    db_session.commit()

    calls: list[str] = []
    with patch(
        "app.services.location_geo_service.lookup_address",
        side_effect=_guarded(
            db_session,
            lambda lat, lon: {"country": "Россия", "region": "Москва", "city": "Москва"},
            calls,
            "lookup_address",
        ),
    ):
        result = backfill_location_geo(db_session, limit=200, platform_codes=("five_verst",))

    assert len(calls) == result.candidates_total
    for location in locations:
        db_session.refresh(location)
        assert (location.country, location.region, location.city) == ("Россия", "Москва", "Москва")


def test_backfill_location_coords_fetches_outside_transaction(db_session: Session) -> None:
    """Добор координат 5 вёрст: прошлая локация закоммичена до следующего фетча."""
    from app.services.location_geo_service import backfill_location_geo

    platform = _platform(db_session, "five_verst")
    slugs = [f"coords-guard-{uuid4().hex[:8]}" for _ in range(2)]
    db_session.add_all(
        Location(
            platform_id=platform.id,
            external_key=slug,
            name=f"000 Coords Guard {index}",
            source_url=f"https://5verst.ru/{slug}/",
            is_official_map=True,
        )
        for index, slug in enumerate(slugs)
    )
    db_session.commit()

    def fake_fetch(slug: str):
        mine = slug in slugs
        return (
            CanonicalLocation(
                external_key=slug,
                name="Coords Guard",
                country="Россия" if mine else None,
                region="Москва" if mine else None,
                city="Москва" if mine else None,
                latitude=55.7 if mine else None,
                longitude=37.6 if mine else None,
                source_url=f"https://5verst.ru/{slug}/",
            ),
            "<html></html>",
        )

    calls: list[str] = []
    with (
        patch(
            "app.platform_adapters.five_verst.bulk_parser.fetch_location",
            side_effect=_guarded(db_session, fake_fetch, calls, "fetch_location"),
        ),
        patch("app.sync.five_verst_locations._enrich_location_geo", side_effect=lambda loc: loc),
    ):
        result = backfill_location_geo(db_session, limit=200, fetch_missing_coordinates=True)

    assert result.errors == []
    assert len(calls) == result.candidates_total
    rows = db_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key.in_(slugs)).all()
    assert sorted((row.latitude, row.longitude) for row in rows) == [(55.7, 37.6), (55.7, 37.6)]
