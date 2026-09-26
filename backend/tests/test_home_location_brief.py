"""Домашняя локация в ответе /auth/me: {slug, name} или null.

Стережём две вещи: выбор вручную побеждает автоматику, а автоматика берёт
локацию с наибольшим числом пробежек (склеивая системы через каталог). И что
/auth/me не падает и не считает каталог сам, когда в Redis его нет.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services import home_location_brief_service, location_page_service
from app.services.home_location_brief_service import home_location_brief, reset_home_location_memo
from app.services.location_catalog_service import LocationCatalogIndex, identity_key_by_location_id


@pytest.fixture(autouse=True)
def _fresh_memo() -> Generator[None, None, None]:
    reset_home_location_memo()
    yield
    reset_home_location_memo()


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _location(db_session: Session, platform: Platform, name: str) -> Location:
    location = Location(platform_id=platform.id, external_key=f"hlb-{uuid4().hex[:10]}", name=name, city="Город")
    db_session.add(location)
    db_session.flush()
    return location


def _runs(db_session: Session, participant: Participant, location: Location, count: int, start: date) -> None:
    for index in range(count):
        event = Event(
            platform_id=location.platform_id,
            location_id=location.id,
            external_event_key=f"hlb-ev-{uuid4().hex[:10]}",
            event_date=start + timedelta(days=7 * index),
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"hlb-res-{uuid4().hex[:10]}",
                position=1,
            )
        )
    db_session.flush()


def _runner(db_session: Session, platform: Platform) -> tuple[User, Participant]:
    participant = Participant(
        platform_id=platform.id, external_user_id=f"hlb-{uuid4().hex[:12]}", display_name="Домов Тест"
    )
    user = User(consent_accepted=True)
    db_session.add_all([participant, user])
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url="https://example.org",
        )
    )
    db_session.flush()
    return user, participant


def _catalog_node(db_session: Session, location: Location, name: str) -> str:
    catalog = LocationCatalog(canonical_name=name)
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(
            catalog_id=catalog.id,
            platform_id=location.platform_id,
            external_key=location.external_key,
            location_id=location.id,
        )
    )
    db_session.flush()
    return f"catalog:{catalog.id}"


def _index(monkeypatch: pytest.MonkeyPatch, items: list[dict] | None) -> None:
    payload = None if items is None else {"items": items, "series": [], "total": len(items)}
    monkeypatch.setattr(location_page_service, "_read_locations_index_cache", lambda: payload)


def test_manual_choice_wins(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    five = _platform(db_session, "five_verst")
    user, participant = _runner(db_session, five)
    busy = _location(db_session, five, "Частая")
    _runs(db_session, participant, busy, 5, date(2026, 1, 3))
    busy_key = _catalog_node(db_session, busy, "Частая")
    user.home_location_key = "catalog:manual-choice"
    _index(
        monkeypatch,
        [
            {"identity_key": busy_key, "slug": "chastaya", "name": "Частая"},
            {"identity_key": "catalog:manual-choice", "slug": "lyubimaya", "name": "Любимая"},
        ],
    )

    assert home_location_brief(db_session, user) == {"slug": "lyubimaya", "name": "Любимая"}


def test_auto_picks_most_runs_across_platforms(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    five = _platform(db_session, "five_verst")
    runpark = _platform(db_session, "runpark")
    user, participant = _runner(db_session, five)
    second = Participant(platform_id=runpark.id, external_user_id=f"hlb-{uuid4().hex[:12]}", display_name="Домов Тест")
    db_session.add(second)
    db_session.flush()
    # Привязка без participant_id: участник находится по (система, внешний id).
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=runpark.id,
            participant_id=None,
            external_user_id=second.external_user_id,
            external_url="https://example.org",
        )
    )
    three_here = _location(db_session, five, "Три пробежки")
    _runs(db_session, participant, three_here, 3, date(2026, 1, 3))
    # Одна площадка в двух системах: 2 + 2 = 4 — больше, чем 3.
    merged_five = _location(db_session, five, "Склеенная")
    merged_runpark = _location(db_session, runpark, "Склеенная")
    _runs(db_session, participant, merged_five, 2, date(2025, 1, 4))
    _runs(db_session, second, merged_runpark, 2, date(2025, 6, 7))
    merged_key = _catalog_node(db_session, merged_five, "Склеенная")
    db_session.add(
        LocationCatalogLink(
            catalog_id=UUID(merged_key.split(":", 1)[1]),
            platform_id=runpark.id,
            external_key=merged_runpark.external_key,
            location_id=merged_runpark.id,
        )
    )
    db_session.flush()
    _index(
        monkeypatch,
        [
            {"identity_key": f"location:{three_here.id}", "slug": "tri", "name": "Три пробежки"},
            {"identity_key": merged_key, "slug": "skleennaya", "name": "Склеенная"},
        ],
    )

    assert home_location_brief(db_session, user) == {"slug": "skleennaya", "name": "Склеенная"}


def test_no_runs_or_no_catalog_gives_none(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    five = _platform(db_session, "five_verst")
    user, participant = _runner(db_session, five)
    _index(monkeypatch, [{"identity_key": "catalog:x", "slug": "x", "name": "X"}])
    assert home_location_brief(db_session, user) is None

    place = _location(db_session, five, "Есть пробежки")
    _runs(db_session, participant, place, 1, date(2026, 1, 3))
    reset_home_location_memo()
    # Каталог в Redis не прогрет — сами его не считаем.
    _index(monkeypatch, None)
    called: list[bool] = []
    monkeypatch.setattr(location_page_service, "build_locations_index", lambda *_a, **_k: called.append(True))
    assert home_location_brief(db_session, user) is None
    assert called == []


def test_catalog_map_is_memoized(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[bool] = []

    def read() -> dict:
        reads.append(True)
        return {"items": [{"identity_key": "catalog:m", "slug": "m", "name": "M"}], "series": []}

    monkeypatch.setattr(location_page_service, "_read_locations_index_cache", read)
    user = User(consent_accepted=True, home_location_key="catalog:m")
    db_session.add(user)
    db_session.flush()
    for _ in range(3):
        assert home_location_brief(db_session, user) == {"slug": "m", "name": "M"}
    assert len(reads) == 1
    assert home_location_brief_service._memo["map"]


def test_auth_me_returns_home_location(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(id=uuid4(), consent_accepted=True, home_location_key="catalog:me-home")
    db_session.add(user)
    db_session.flush()
    _index(monkeypatch, [{"identity_key": "catalog:me-home", "slug": "meshchersky", "name": "Мещерский"}])
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(app_secret_key="test")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/api/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["home_location"] == {"slug": "meshchersky", "name": "Мещерский"}


def test_auto_matches_catalog_slugs_like_the_catalog_index(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Слаг parkrun в связке каталога и у локации пишут по-разному: «nizhny-prud» и «nizhnyprud».

    Без нормализации пробежки на таких локациях выпадали из подсчёта, и «Моя:»
    показывала не ту локацию (ревью 26.09.2026, HOME-4: 55 parkrun-локаций
    копии прода, «Нижний пруд» стал «Зеленоградом»).
    """
    parkrun = _platform(db_session, "parkrun")
    five = _platform(db_session, "five_verst")
    user, participant = _runner(db_session, parkrun)
    tag = uuid4().hex[:8]
    pond = Location(platform_id=parkrun.id, external_key=f"nizhnyprud{tag}", name="Nizhny Prud", city="Город")
    old = Location(platform_id=parkrun.id, external_key=f"staryslug{tag}", name="Stary Park", city="Город")
    db_session.add_all([pond, old])
    db_session.flush()
    elsewhere = _location(db_session, five, "Другая")
    pond_node = LocationCatalog(canonical_name="Нижний пруд")
    old_node = LocationCatalog(canonical_name="Старый парк", legacy_parkrun_slug=f"stary-slug-{tag}")
    db_session.add_all([pond_node, old_node])
    db_session.flush()
    # Связки без location_id — только слагом системы, записанным через дефис.
    db_session.add_all(
        [
            LocationCatalogLink(catalog_id=pond_node.id, platform_id=parkrun.id, external_key=f"nizhny-prud-{tag}"),
            LocationCatalogLink(catalog_id=old_node.id, platform_id=parkrun.id, external_key=f"novy-slug-{tag}"),
        ]
    )
    db_session.flush()
    _runs(db_session, participant, pond, 4, date(2021, 1, 2))
    _runs(db_session, participant, old, 3, date(2020, 1, 4))
    _runs(db_session, participant, elsewhere, 2, date(2026, 1, 3))
    _index(
        monkeypatch,
        [
            {"identity_key": f"catalog:{pond_node.id}", "slug": "nizhny-prud", "name": "Нижний пруд"},
            {"identity_key": f"catalog:{old_node.id}", "slug": "stary-park", "name": "Старый парк"},
            {"identity_key": f"location:{elsewhere.id}", "slug": "drugaya", "name": "Другая"},
        ],
    )

    assert home_location_brief(db_session, user) == {"slug": "nizhny-prud", "name": "Нижний пруд"}
    # Ключи узлов — ровно те, что даёт эталон (LocationCatalogIndex), в том
    # числе по старому слагу parkrun, записанному по-другому.
    reference = identity_key_by_location_id(db_session, LocationCatalogIndex(db_session))
    ids = [pond.id, old.id, elsewhere.id]
    assert home_location_brief_service._identity_keys(db_session, ids) == {
        location_id: reference[location_id] for location_id in ids
    }
    assert reference[old.id] == f"catalog:{old_node.id}"
