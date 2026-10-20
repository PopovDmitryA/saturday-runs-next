"""Новая parkrun-площадка из профиля сразу встаёт на карту.

10.10.2026 Дмитрий пробежал первый старт Okura kaigan koen (Япония). Площадка
открылась после снимка каталога 11.08.2026, и импорт профиля завёл её на
транслитерации: ключ `okura-kaigan-koen` вместо `okurakaigankoen`, ссылка на
parkrun.org.uk вместо parkrun.jp, ни страны, ни координат — челлендж «Страны»
Японию не засчитал. Здесь сторожим всё, что чинилось:

* снимки каталога знают площадки, открытые после 11.08, и не теряют закрытые;
* импорт профиля ставит новой площадке точку, страну и ссылку на домен страны;
* импорт не затирает выверенную ссылку догадкой parkrun.org.uk;
* отставший снимок виден в ежедневной сводке админу.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, RunResult
from app.parkrun.event_slugs import CatalogEvent, catalog_event, official_event_slug
from app.parkrun.location_keys import result_key_with_slug
from app.parkrun.parsers.athlete import _slugify_event
from app.platform_adapters.canonical import CanonicalLocation, CanonicalRunResult
from app.services.vk_admin_notify import format_daily_summary
from app.sync import upsert
from app.workers.tasks.admin_digest import parkrun_catalog_gaps


def _parkrun(db: Session) -> Platform:
    row = db.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if row is None:
        pytest.skip("platform parkrun not seeded")
    return row


@pytest.fixture(autouse=True)
def _no_parkrun_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Импорт после записи догружает профили участников с parkrun — тут не нужно."""
    monkeypatch.setattr(upsert, "_discover_participants_after_protocol_upsert", lambda *args, **kwargs: None)


@pytest.fixture
def geocode(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    """Nominatim без сети: отвечает Акаси и запоминает, о какой точке спросили."""
    calls: list[tuple[float, float]] = []

    def fake_lookup(latitude: float, longitude: float) -> dict[str, str | None]:
        calls.append((latitude, longitude))
        return {"country": "Япония", "region": "Хиого", "city": "Акаси"}

    monkeypatch.setattr("app.geo.reverse_geocode.lookup_address", fake_lookup)
    return calls


def _run(slug: str, name: str, *, event_number: int = 1) -> CanonicalRunResult:
    return CanonicalRunResult(
        external_result_key=f"parkrun:7035519:{slug}:2026-10-10:{event_number}",
        external_user_id=f"test-{uuid4().hex[:8]}",
        participant_name="Тестовый Бегун",
        event_date=date(2026, 10, 10),
        event_number=event_number,
        location_external_key=slug,
        location_name=name,
        position=12,
        finish_time_sec=1389,
        finish_time_display="23:09",
    )


def test_snapshot_knows_events_opened_after_august() -> None:
    assert _slugify_event("Okura kaigan koen") == "okurakaigankoen"
    event = catalog_event("okurakaigankoen")
    assert event is not None
    assert (event.latitude, event.longitude) == (34.642379, 135.008025)
    assert event.page_url == "https://www.parkrun.jp/okurakaigankoen/"
    assert event.results_url(1) == "https://www.parkrun.jp/okurakaigankoen/results/1/"


def test_snapshot_keeps_closed_events() -> None:
    """Закрытая площадка из живого каталога уходит, а из снимка — нет.

    Её строка в базе уже под официальным слагом; забудь его снимок — парсер
    вернётся к транслитерации и заведёт дубль локации.
    """
    assert official_event_slug("Grüner Weg") == "gruenerweg"
    assert official_event_slug("Sand River, Virginia") == "sandriver"


def test_catalog_event_ignores_transliteration_and_russia() -> None:
    # Без дефисов транслитерация совпала бы с площадкой архива, но ссылка
    # parkrun.jp/okura-kaigan-koen/ — 404.
    assert catalog_event("okura-kaigan-koen") is None
    # Российская площадка в снимке есть (слаг из архива), а домена parkrun.ru
    # больше нет — ссылку на неё не строим.
    russian = catalog_event("kuzminki")
    assert russian is not None and russian.page_url is None and russian.results_url(1) is None
    assert catalog_event(None) is None


def test_dump_catalog_merges_with_previous_snapshot(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import parkrun_catalog_sync

    dump = tmp_path / "events_slugs.json"
    dump.write_text(json.dumps({"gruenerweg": "gruenerweg", "abbeypark": "abbey-park-old"}), encoding="utf-8")
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps(
            {
                "countries": {"97": {"url": "www.parkrun.org.uk"}},
                "events": {
                    "features": [
                        {
                            "geometry": {"coordinates": [-1.13, 52.64]},
                            "properties": {"eventname": "abbeypark", "EventShortName": "Abbey Park", "countrycode": 97},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(parkrun_catalog_sync, "CATALOG_DUMP", dump)
    monkeypatch.setattr(sys, "argv", ["parkrun_catalog_sync.py", "--events-json", str(events), "--dump-catalog"])

    assert parkrun_catalog_sync.main() == 0
    # Закрытая осталась, переименованная взяла слаг свежего каталога.
    assert json.loads(dump.read_text(encoding="utf-8")) == {"abbeypark": "abbeypark", "gruenerweg": "gruenerweg"}


def test_sync_without_network_uses_bundled_snapshots() -> None:
    """`parkrun_catalog_sync.py --no-fetch` сверяет по снимкам из образа — на проде без сети."""
    from scripts.parkrun_catalog_sync import build_index, snapshot_catalog

    by_slug, by_name = build_index(snapshot_catalog())
    okura = by_name["okurakaigankoen"]
    assert (okura.slug, okura.page_url) == ("okurakaigankoen", "https://www.parkrun.jp/okurakaigankoen/")
    assert by_slug["gruenerweg"].page_url == "https://www.parkrun.com.de/gruenerweg/"  # закрытая — из снимка


def test_profile_import_puts_new_event_on_the_map(
    db_session: Session, geocode, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform = _parkrun(db_session)
    # Площадка из снимка, но со своим слагом: тест не зависит от того, есть ли
    # в базе стенда настоящая Okura.
    slug = f"okurakaigankoen{uuid4().hex[:6]}"
    event = CatalogEvent(slug=slug, country_code=46, latitude=34.642379, longitude=135.008025)
    monkeypatch.setattr(upsert, "resolve_catalog_event", lambda db, value: event if value == slug else None)
    name = f"Okura kaigan koen {slug[-6:]}"

    upsert.import_profile_run_results(db_session, platform, [_run(slug, name)])

    row = db_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
    assert (row.latitude, row.longitude) == (34.642379, 135.008025)
    assert row.country == "Япония"
    assert row.city == "Акаси"
    assert row.region is None  # «Хиого»/«JP-28» не нужен: у зарубежных площадок региона нет
    assert row.source_url == f"https://www.parkrun.jp/{slug}/"
    assert geocode == [(34.642379, 135.008025)]
    protocol = db_session.query(Event).filter(Event.location_id == row.id).one()
    assert protocol.source_url == f"https://www.parkrun.jp/{slug}/results/1/"

    # Повторный импорт в сеть не ходит: точка уже есть.
    upsert.import_profile_run_results(db_session, platform, [_run(slug, name)])
    assert len(geocode) == 1


def test_profile_import_keeps_verified_source_url(db_session: Session, geocode) -> None:
    """Догадка parkrun.org.uk/<слаг>/ не затирает ссылку, выверенную сверкой."""
    platform = _parkrun(db_session)
    slug = f"closed-{uuid4().hex[:8]}"  # площадки нет в снимке
    verified = f"https://www.parkrun.co.za/{slug}/"
    upsert.upsert_location(
        db_session,
        platform,
        CanonicalLocation(external_key=slug, name="Closed Park", source_url=verified),
    )

    upsert.import_profile_run_results(db_session, platform, [_run(slug, "Closed Park")])

    row = db_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
    assert row.source_url == verified
    assert row.latitude is None
    assert geocode == []


def test_profile_import_unknown_event_still_gets_fallback_url(db_session: Session, geocode) -> None:
    platform = _parkrun(db_session)
    slug = f"brand-new-{uuid4().hex[:8]}"

    upsert.import_profile_run_results(db_session, platform, [_run(slug, "Brand New Park")])

    row = db_session.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one()
    assert row.source_url == f"https://www.parkrun.org.uk/{slug}/"
    assert row.country is None
    assert geocode == []


def test_digest_lists_new_locations_missing_from_snapshot(db_session: Session) -> None:
    platform = _parkrun(db_session)
    now = datetime.now(timezone.utc)
    fresh = f"Fresh Park {uuid4().hex[:6]}"
    placed = f"Placed Park {uuid4().hex[:6]}"
    old = f"Old Park {uuid4().hex[:6]}"
    db_session.add_all(
        [
            Location(platform_id=platform.id, external_key=uuid4().hex, name=fresh),
            Location(platform_id=platform.id, external_key=uuid4().hex, name=placed, latitude=1.0, longitude=2.0),
            Location(
                platform_id=platform.id,
                external_key=uuid4().hex,
                name=old,
                created_at=now - timedelta(days=30),
            ),
        ]
    )
    db_session.flush()

    gaps = parkrun_catalog_gaps(db_session, now=now)

    assert any(line.startswith(fresh) for line in gaps)
    assert not any(line.startswith(placed) or line.startswith(old) for line in gaps)

    text = format_daily_summary("10.10.2026", [], parkrun_catalog_gaps=["Okura kaigan koen (10.10)"])
    assert "сверка с каталогом" in text
    assert "• Okura kaigan koen (10.10)" in text
    assert "сверка с каталогом" not in format_daily_summary("10.10.2026", [])


def test_result_key_with_slug() -> None:
    assert (
        result_key_with_slug("parkrun:7035519:okura-kaigan-koen:2026-10-10:1", "okurakaigankoen")
        == "parkrun:7035519:okurakaigankoen:2026-10-10:1"
    )
    assert result_key_with_slug("parkrun:a:b:c", "x") == "parkrun:a:b:c"
    assert result_key_with_slug("five_verst:1:2:3:4", "x") == "five_verst:1:2:3:4"


def test_profile_import_writes_location_slug_into_result_key(db_session: Session, geocode) -> None:
    """Парсер прислал транслитерацию, локация уже под слагом parkrun — ключ результата тоже под ним."""
    platform = _parkrun(db_session)
    tag = uuid4().hex[:6]
    official = f"okurakaigankoen{tag}"
    db_session.add(Location(platform_id=platform.id, external_key=official, name=f"Okura kaigan koen {tag}"))
    db_session.flush()

    upsert.import_profile_run_results(db_session, platform, [_run(f"okura-kaigan-koen-{tag}", f"Okura kaigan koen {tag}")])

    row = (
        db_session.query(RunResult)
        .join(Event, Event.id == RunResult.event_id)
        .join(Location, Location.id == Event.location_id)
        .filter(Location.external_key == official)
        .one()
    )
    assert row.external_result_key == f"parkrun:7035519:{official}:2026-10-10:1"


def test_profile_import_stays_on_location_of_parser_slug(db_session: Session, geocode, monkeypatch: pytest.MonkeyPatch) -> None:
    """parkrun сменил слаг, имя прежнее, лечение ещё не прошло — второй локации не заводим."""
    platform = _parkrun(db_session)
    tag = uuid4().hex[:6]
    old, new, name = f"oldslug{tag}", f"newslug{tag}", f"Renamed Park {tag}"
    db_session.add(Location(platform_id=platform.id, external_key=old, name=name))
    db_session.flush()
    monkeypatch.setattr(upsert, "is_catalog_slug", lambda db, slug: slug == new)
    monkeypatch.setattr(upsert, "official_slug_by_name", lambda db, value: new)

    upsert.import_profile_run_results(db_session, platform, [_run(old, name)])

    keys = {row[0] for row in db_session.query(Location.external_key).filter(Location.platform_id == platform.id, Location.name == name)}
    assert keys == {old}
