"""Один слаг на parkrun-площадку: в локации, протоколах, результатах и каталоге.

16.10.2026 на проде были 56 пар локаций-двойников (`angarskie-prudy` и
`angarskieprudy`), 37 локаций со слагом не как у parkrun и 63 тыс. протоколов со
старым слагом в ключе. Сторожим правило parkrun (строчные буквы и цифры,
дефис только в `-juniors`), снимок каталога, данные каталога локаций в репо и
обе операции: переименование и слияние двойников.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    RunResult,
    StartWeather,
)
from app.parkrun.event_slugs import CATALOG_PATH, catalog_event, official_event_slug
from app.parkrun.location_keys import ParkrunKeyConflict, merge_parkrun_locations, rename_parkrun_location
from app.platform_adapters.canonical import CanonicalRunResult
from app.sync import upsert

REPO_ROOT = Path(__file__).resolve().parents[2]
PARKRUN_SLUG = re.compile(r"[a-z0-9]+(-juniors)?")
FETCHED = datetime(2022, 1, 9, tzinfo=timezone.utc)


def test_snapshot_slugs_follow_parkrun_rule() -> None:
    slugs = set(json.loads(CATALOG_PATH.read_text(encoding="utf-8")).values())
    bad = sorted(slug for slug in slugs if not PARKRUN_SLUG.fullmatch(slug))
    assert bad == []


def test_snapshot_knows_russian_and_renamed_events() -> None:
    # Российские — из архивных снимков каталога: без дефисов, как у parkrun.
    assert official_event_slug("Gorky Park") == "gorkypark"
    assert official_event_slug("Angarskie Prudy") == "angarskieprudy"
    assert official_event_slug("park 30-letiya Oktyabrya") == "park30letiyaoktyabrya"
    # parkrun переименовал площадку вместе со слагом, а профиль показывает
    # новое имя за всю историю — старое имя тоже ведёт к новому слагу.
    assert official_event_slug("Ludlow") == "mortimerforest"
    assert official_event_slug("Mortimer Forest") == "mortimerforest"
    # Домена у ушедшей из parkrun России нет — ссылку не строим.
    russian = catalog_event("gorkypark")
    assert russian is not None and russian.page_url is None


def test_location_catalog_json_uses_official_parkrun_slugs() -> None:
    official = set(json.loads(CATALOG_PATH.read_text(encoding="utf-8")).values())
    items = json.loads((REPO_ROOT / "data" / "location_catalog.json").read_text(encoding="utf-8"))
    bad: list[str] = []
    for item in items:
        legacy = item.get("legacy_parkrun_slug")
        if legacy and legacy not in official:
            bad.append(f"legacy {legacy}")
        parkrun_links = [link["external_key"] for link in item.get("links") or [] if link.get("platform") == "parkrun"]
        bad.extend(f"link {key}" for key in parkrun_links if key not in official)
        if len(parkrun_links) > 1:
            bad.append(f"{item.get('canonical_name')}: {len(parkrun_links)} parkrun-связки")
    assert bad == []


@pytest.fixture
def parkrun(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if row is None:
        pytest.skip("platform parkrun not seeded")
    return row


@pytest.fixture(autouse=True)
def _no_parkrun_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upsert, "_discover_participants_after_protocol_upsert", lambda *args, **kwargs: None)


def _location(db: Session, platform: Platform, key: str, **fields) -> Location:
    row = Location(platform_id=platform.id, external_key=key, name=fields.pop("name", key), **fields)
    db.add(row)
    db.flush()
    return row


def _participant(db: Session, platform: Platform) -> Participant:
    row = Participant(platform_id=platform.id, external_user_id=f"9{uuid4().int % 10**8}", display_name="Тест")
    db.add(row)
    db.flush()
    return row


def _event(db: Session, platform: Platform, location: Location, day: date, number: int) -> Event:
    row = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{day.isoformat()}:{location.external_key}",
        event_date=day,
        event_number=number,
        source_url=f"https://www.parkrun.org.uk/{location.external_key}/results/{number}/",
    )
    db.add(row)
    db.flush()
    return row


def _result(
    db: Session, platform: Platform, event: Event, participant: Participant | None, slug: str, position: int
) -> RunResult:
    if participant is None:  # заглушка «неизвестного»: без участника, в конце ключа место
        key = f"parkrun:unknown:{slug}:{event.event_date.isoformat()}:{position}"
    else:
        key = f"parkrun:{participant.external_user_id}:{slug}:{event.event_date.isoformat()}:{event.event_number}"
    row = RunResult(
        event_id=event.id,
        participant_id=participant.id if participant is not None else None,
        external_result_key=key,
        position=position,
        finish_time_sec=1500 + position,
    )
    db.add(row)
    db.flush()
    return row


def test_import_uses_existing_twin_instead_of_new_location(db_session: Session, parkrun: Platform) -> None:
    tag = uuid4().hex[:8]
    twin = _location(db_session, parkrun, f"gorky-park-{tag}", name=f"Gorky Park {tag}")
    official = f"gorkypark{tag}"

    upsert.import_profile_run_results(
        db_session,
        parkrun,
        [
            CanonicalRunResult(
                external_result_key=f"parkrun:1:{official}:2021-06-05:100",
                external_user_id=f"8{tag[:6]}",
                participant_name="Тест",
                event_date=date(2021, 6, 5),
                event_number=100,
                location_external_key=official,
                location_name=f"Gorky Park {tag}",
                position=5,
                finish_time_sec=1300,
                finish_time_display="21:40",
            )
        ],
    )

    keys = {
        row[0]
        for row in db_session.query(Location.external_key)
        .filter(Location.platform_id == parkrun.id, Location.external_key.in_([twin.external_key, official]))
        .all()
    }
    assert keys == {twin.external_key}
    assert db_session.query(Event).filter(Event.location_id == twin.id).count() == 1


def test_rename_moves_key_everywhere(db_session: Session, parkrun: Platform) -> None:
    tag = uuid4().hex[:8]
    old, new = f"lesopark-severny-{tag}", f"lesoparkseverny{tag}"
    location = _location(db_session, parkrun, old, source_url=f"https://www.parkrun.org.uk/{old}/")
    node = LocationCatalog(canonical_name=f"Тест {tag}", legacy_parkrun_slug=old)
    db_session.add(node)
    db_session.flush()
    db_session.add(LocationCatalogLink(catalog_id=node.id, platform_id=parkrun.id, external_key=old, location_id=location.id))
    runner = _participant(db_session, parkrun)
    event = _event(db_session, parkrun, location, date(2021, 6, 5), 120)
    _result(db_session, parkrun, event, runner, old, 3)
    _result(db_session, parkrun, event, None, old, 7)

    rewrite = rename_parkrun_location(db_session, location, new)
    db_session.expire_all()

    location = db_session.get(Location, location.id)
    assert location.external_key == new
    assert location.source_url == f"https://www.parkrun.org.uk/{new}/"
    link_key = db_session.query(LocationCatalogLink.external_key).filter(LocationCatalogLink.catalog_id == node.id).scalar()
    assert link_key == new
    assert db_session.get(LocationCatalog, node.id).legacy_parkrun_slug == new
    event = db_session.get(Event, event.id)
    assert event.external_event_key == f"2021-06-05:{new}"
    assert event.source_url == f"https://www.parkrun.org.uk/{new}/results/120/"
    keys = {row.external_result_key for row in db_session.query(RunResult).filter(RunResult.event_id == event.id)}
    assert keys == {f"parkrun:{runner.external_user_id}:{new}:2021-06-05:120", f"parkrun:unknown:{new}:2021-06-05:7"}
    assert (rewrite.events, rewrite.results) == (1, 2)


def test_rename_refuses_taken_key(db_session: Session, parkrun: Platform) -> None:
    tag = uuid4().hex[:8]
    _location(db_session, parkrun, f"ab{tag}")
    other = _location(db_session, parkrun, f"a-b-{tag}")
    with pytest.raises(ParkrunKeyConflict):
        rename_parkrun_location(db_session, other, f"ab{tag}")


def test_merge_moves_everything_to_official_half(db_session: Session, parkrun: Platform) -> None:
    tag = uuid4().hex[:8]
    keep = _location(db_session, parkrun, f"greenpoint{tag}", latitude=-33.9, longitude=18.4)
    drop = _location(db_session, parkrun, f"green-point-{tag}", timezone="Africa/Johannesburg")
    node = LocationCatalog(canonical_name=f"Green Point {tag}")
    db_session.add(node)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(catalog_id=node.id, platform_id=parkrun.id, external_key=drop.external_key, location_id=drop.id)
    )
    alice, bob = _participant(db_session, parkrun), _participant(db_session, parkrun)
    # Своя дата у двойника — протокол переезжает целиком.
    only_drop = _event(db_session, parkrun, drop, date(2022, 1, 1), 10)
    _result(db_session, parkrun, only_drop, alice, drop.external_key, 1)
    # Одна дата у обоих: Алиса — дубль, Боб есть только у двойника.
    keep_day = _event(db_session, parkrun, keep, date(2022, 1, 8), 11)
    _result(db_session, parkrun, keep_day, alice, keep.external_key, 1)
    drop_day = _event(db_session, parkrun, drop, date(2022, 1, 8), 11)
    _result(db_session, parkrun, drop_day, alice, drop.external_key, 1)
    _result(db_session, parkrun, drop_day, bob, drop.external_key, 2)
    # Заглушки: на занятом у основной месте 1 — лишняя, на своей дате — переезжает.
    _result(db_session, parkrun, drop_day, None, drop.external_key, 1)
    _result(db_session, parkrun, only_drop, None, drop.external_key, 5)
    # Погода: у двойника окончательная (era5) на общую дату и своя дата.
    db_session.add_all(
        [
            StartWeather(location_id=keep.id, obs_date=date(2022, 1, 8), start_time_local=time(9, 0), source="archive", fetched_at=FETCHED),
            StartWeather(location_id=drop.id, obs_date=date(2022, 1, 8), start_time_local=time(9, 0), source="era5", fetched_at=FETCHED),
            StartWeather(location_id=drop.id, obs_date=date(2022, 1, 1), start_time_local=time(9, 0), source="era5", fetched_at=FETCHED),
        ]
    )
    db_session.flush()
    drop_id, keep_id = drop.id, keep.id

    report = merge_parkrun_locations(db_session, keep, drop)
    db_session.expire_all()

    assert db_session.get(Location, drop_id) is None
    keep = db_session.get(Location, keep_id)
    events = db_session.query(Event).filter(Event.location_id == keep_id).order_by(Event.event_date).all()
    assert [e.external_event_key for e in events] == [f"2022-01-01:{keep.external_key}", f"2022-01-08:{keep.external_key}"]
    runners = sorted(str(r.participant_id) for r in db_session.query(RunResult).filter(RunResult.event_id == events[1].id))
    assert runners == sorted([str(alice.id), str(bob.id)])  # дубль Алисы и заглушка удалены, Боб перенесён
    placeholders = [
        r.external_result_key
        for r in db_session.query(RunResult).filter(RunResult.event_id == events[0].id, RunResult.participant_id.is_(None))
    ]
    assert placeholders == [f"parkrun:unknown:{keep.external_key}:2022-01-01:5"]
    assert None not in report.participants and report.participants == {alice.id, bob.id}
    result_keys = [r.external_result_key for r in db_session.query(RunResult).join(Event).filter(Event.location_id == keep_id)]
    assert result_keys and all(f":{keep.external_key}:" in key for key in result_keys)
    sources = {row.obs_date: row.source for row in db_session.query(StartWeather).filter(StartWeather.location_id == keep_id)}
    assert sources == {date(2022, 1, 1): "era5", date(2022, 1, 8): "era5"}
    link = db_session.query(LocationCatalogLink).filter(LocationCatalogLink.catalog_id == node.id).one()
    assert (link.location_id, link.external_key) == (keep_id, keep.external_key)
    assert keep.timezone == "Africa/Johannesburg"
    assert (report.events_moved, report.events_merged, report.results_dropped) == (1, 1, 2)


def test_merge_stops_on_unknown_conflict(db_session: Session, parkrun: Platform) -> None:
    """Строка двойника, которую слияние не умеет разобрать, — повод остановиться, а не удалять."""
    tag = uuid4().hex[:8]
    keep = _location(db_session, parkrun, f"xy{tag}")
    drop = _location(db_session, parkrun, f"x-y-{tag}")
    for location in (keep, drop):
        db_session.execute(
            text("insert into location_descriptions (location_id, course_text) values (:id, 'трасса')"),
            {"id": location.id},
        )
    with pytest.raises(ParkrunKeyConflict):
        merge_parkrun_locations(db_session, keep, drop)


def test_import_finds_twin_by_transliterated_name(db_session: Session, parkrun: Platform) -> None:
    """Снимок знает слаг parkrun, а в базе с прежних времён транслитерация с потерянными буквами."""
    tag = uuid4().hex[:6]
    legacy = _location(db_session, parkrun, f"historic-k-p-bridge-{tag}", name=f"Historic Kōpū Bridge {tag}")
    cache: dict[str, str] = {}
    key = upsert._existing_parkrun_key(db_session, parkrun, f"historickopubridge{tag}", f"Historic Kōpū Bridge {tag}", cache)
    assert key == legacy.external_key
    assert cache == {}  # неточное совпадение не кэшируем: двойника могут переименовать посреди импорта


def test_plan_merges_pairs_and_renames_singles(db_session: Session, parkrun: Platform, monkeypatch) -> None:
    from scripts import unify_parkrun_location_keys as unify

    tag = uuid4().hex[:6]
    official_pair, official_single = f"angarskieprudy{tag}", f"gorkypark{tag}"
    keep = _location(db_session, parkrun, official_pair)
    drop = _location(db_session, parkrun, f"angarskie-prudy-{tag}")
    single = _location(db_session, parkrun, f"gorky-park-{tag}")
    far = _location(db_session, parkrun, f"far-away-{tag}", name=f"Tezka {tag}", latitude=10.0, longitude=10.0)
    snapshot = {official_pair: official_pair, official_single: official_single, f"tezka{tag}": f"tezka{tag}"}
    monkeypatch.setattr(unify, "_catalog", lambda: snapshot)
    monkeypatch.setattr(unify, "official_event_slug", lambda name: snapshot.get(re.sub(r"[^0-9a-z]+", "", name.lower())))
    monkeypatch.setattr(
        unify,
        "catalog_event",
        lambda slug: type("E", (), {"latitude": 50.0, "longitude": 50.0})() if slug in snapshot.values() else None,
    )

    plan = unify.build_plan(db_session, parkrun)

    merges = {(k.id, d.id) for k, d, _ in plan["merges"]}
    renames = {(loc.id, slug) for loc, slug in plan["renames"]}
    assert (keep.id, drop.id) in merges
    assert (single.id, official_single) in renames
    # Тёзка в 5 тыс. км от площадки каталога — не она.
    assert all(loc.id != far.id for loc, _ in plan["renames"])
    assert any(item["key"] == far.external_key for item in plan["name_only"])


def test_dump_catalog_follows_parkrun_renames(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import parkrun_catalog_sync

    archive = tmp_path / "events_archive.json"
    archive.write_text(json.dumps({"events": {"ludlow": [97, 52.353284, -2.773078]}}), encoding="utf-8")
    monkeypatch.setattr(parkrun_catalog_sync, "ARCHIVE_PATH", archive)
    events = [
        parkrun_catalog_sync.CatalogEvent("mortimerforest", "Mortimer Forest", 52.353284, -2.773078, "www.parkrun.org.uk", ""),
        parkrun_catalog_sync.CatalogEvent("bushy", "Bushy Park", 51.41, -0.33, "www.parkrun.org.uk", ""),
    ]
    renamed = parkrun_catalog_sync.renamed_slugs({"ludlow": "ludlow", "bushypark": "bushy"}, events)
    assert renamed == {"ludlow": "mortimerforest"}


def test_state_journal_round_trip(tmp_path) -> None:
    """Журнал групп переживает обрыв: --finish-only восстанавливает из него участников и локации."""
    from scripts import unify_parkrun_location_keys as unify

    state = tmp_path / "state.jsonl"
    keep, renamed, runner = uuid4(), uuid4(), uuid4()
    unify._record(state, {"kind": "merge", "location_id": keep, "participants": sorted(str(p) for p in {runner})})
    unify._record(state, {"kind": "rename", "location_id": renamed})
    unify._record(state, {"kind": "rewrite", "location_id": uuid4()})

    participants, touched, reshaped = unify._read_state(state)

    assert participants == {runner}
    assert reshaped == {keep, renamed}
    assert len(touched) == 3
