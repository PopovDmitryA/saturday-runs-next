"""Сверка сайта с каталогом parkrun: разбор, журнал изменений, защита, лечение, сообщение.

Этап 2 (18.10.2026): каталог площадок parkrun живёт в боевой базе, сайт сам
сверяется с events.json, лечит свои parkrun-локации и пишет админу в Telegram.
Тесты не ходят в сеть: каталог передаётся явно, Nominatim и Telegram подменены.
Всё пишется внутри транзакции теста и откатывается.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Event, Location, ParkrunCatalogRun, ParkrunEvent, ParkrunEventChange, Platform
from app.parkrun import key_plan
from app.parkrun.catalog_store import active_parkrun_slugs, official_slug_by_name, resolve_catalog_event
from app.parkrun.event_slugs import COUNTRY_DOMAINS
from app.services import parkrun_catalog_notify as notify
from app.services import parkrun_catalog_service as service

NOW = datetime(2026, 10, 18, 6, 20, tzinfo=timezone.utc)


def feature(parkrun_id: int, slug: str, name: str, *, code: int = 97, lat: float = 51.0, lon: float = -1.0, series: int = 1):
    return {
        "id": parkrun_id,
        "geometry": {"coordinates": [lon, lat]},
        "properties": {
            "eventname": slug,
            "EventShortName": name,
            "EventLongName": f"{name} parkrun",
            "countrycode": code,
            "seriesid": series,
            "EventLocation": name,
        },
    }


def payload(features: list[dict]) -> bytes:
    countries = {"0": {"url": None}, **{str(code): {"url": domain} for code, domain in COUNTRY_DOMAINS.items()}}
    return json.dumps({"countries": countries, "events": {"type": "FeatureCollection", "features": features}}).encode()


@pytest.fixture
def factory(db_session: Session):
    """Сессии сверки — на соединении теста: их commit не выходит из его транзакции."""
    connection = db_session.connection()
    db_session.execute(text("delete from parkrun_catalog_runs"))
    db_session.execute(text("delete from parkrun_events"))
    db_session.flush()

    def make() -> Session:
        return Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

    return make


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr(
        "app.services.admin_telegram_notify.send_admin_report",
        lambda text, markup=False: messages.append(text) or True,
    )
    monkeypatch.setattr(
        "app.geo.reverse_geocode.lookup_address",
        lambda lat, lon: {"country": "Япония", "region": None, "city": "Акаси"},
    )
    return messages


def _base(tag: str) -> list[dict]:
    return [feature(9_000_001, f"alpha{tag}", f"Alpha {tag}"), feature(9_000_002, f"beta{tag}", f"Beta {tag}")] + [
        feature(9_100_000 + i, f"filler{i}{tag}", f"Filler {i} {tag}") for i in range(30)
    ]


def test_parse_rejects_protection_page() -> None:
    with pytest.raises(service.CatalogFetchError):
        service.parse_feed(b"<html>captcha</html>")
    with pytest.raises(service.CatalogFetchError):
        service.parse_feed(json.dumps({"events": {"features": []}, "countries": {}}).encode())


def test_first_run_then_unchanged(db_session: Session, factory, sent) -> None:
    tag = uuid4().hex[:6]
    data = payload(_base(tag))

    first = service.run_catalog_sync(factory, payload=data, now=NOW)
    second = service.run_catalog_sync(factory, payload=data, now=NOW + timedelta(days=1))

    assert first["status"] == "ok" and first["catalog_added"] == 32
    assert second["status"] == "unchanged"
    assert official_slug_by_name(db_session, f"Alpha {tag}") == f"alpha{tag}"
    assert len(sent) == 1  # о первом прогоне — сводка, о неизменном — тишина
    assert "➕ **Новые — 32**" in sent[0]


def test_rename_close_move_are_logged(db_session: Session, factory, sent) -> None:
    tag = uuid4().hex[:6]
    service.run_catalog_sync(factory, payload=payload(_base(tag)), now=NOW)
    changed = _base(tag)
    changed[0] = feature(9_000_001, f"alphanew{tag}", f"Alpha New {tag}")  # тот же id — переименование
    changed[1] = feature(9_000_002, f"beta{tag}", f"Beta {tag}", lat=51.1)  # старт перенесли на ~11 км
    del changed[2]  # закрылась
    sent.clear()

    result = service.run_catalog_sync(factory, payload=payload(changed), now=NOW + timedelta(days=1))

    assert result["status"] == "ok"
    assert (result["catalog_renamed"], result["catalog_moved"], result["catalog_closed"]) == (1, 1, 1)
    kinds = {row.kind for row in db_session.query(ParkrunEventChange).filter(ParkrunEventChange.detected_at > NOW).all()}
    assert {"renamed", "moved", "closed"} <= kinds
    # Старое имя площадки остаётся при ней: профиль может прислать его.
    assert official_slug_by_name(db_session, f"Alpha {tag}") == f"alphanew{tag}"
    assert any("Переименованы" in message for message in sent)


def test_truncated_catalog_is_not_applied(db_session: Session, factory, sent) -> None:
    tag = uuid4().hex[:6]
    service.run_catalog_sync(factory, payload=payload(_base(tag)), now=NOW)
    sent.clear()

    result = service.run_catalog_sync(factory, payload=payload(_base(tag)[:5]), now=NOW + timedelta(days=1))

    assert result["status"] == "suspicious"
    assert db_session.query(ParkrunEvent).filter(ParkrunEvent.is_active.is_(True)).count() == 32
    assert len(sent) == 1 and "подозрительный" in sent[0]


def test_alerts_once_then_recovery(db_session: Session, factory, sent, monkeypatch: pytest.MonkeyPatch) -> None:
    tag = uuid4().hex[:6]
    service.run_catalog_sync(factory, payload=payload(_base(tag)), now=NOW)
    sent.clear()

    def broken():
        raise service.CatalogFetchError("ReadTimeout")

    monkeypatch.setattr(service, "fetch_catalog_payload", broken)
    first = service.run_catalog_sync(factory, now=NOW + timedelta(days=1))
    second = service.run_catalog_sync(factory, now=NOW + timedelta(days=2))
    assert (first["status"], second["status"]) == ("failed", "failed")
    assert len(sent) == 1 and "не прошла" in sent[0]  # вторую неудачу подряд не повторяем

    monkeypatch.setattr(service, "fetch_catalog_payload", lambda: payload(_base(tag)))
    service.run_catalog_sync(factory, now=NOW + timedelta(days=3))
    assert any("снова сверяется" in message for message in sent)


def test_heal_renames_transliteration_and_fills_gaps(db_session: Session, factory, sent) -> None:
    tag = uuid4().hex[:6]
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    features = _base(tag) + [feature(9_000_009, f"okurakaigankoen{tag}", f"Okura kaigan koen {tag}", code=46, lat=34.642379, lon=135.008025)]
    location = Location(
        platform_id=platform.id,
        external_key=f"okura-kaigan-koen-{tag}",
        name=f"Okura kaigan koen {tag}",
        source_url=f"https://www.parkrun.org.uk/okura-kaigan-koen-{tag}/",
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"2026-10-10:okura-kaigan-koen-{tag}",
            event_date=date(2026, 10, 10),
            event_number=1,
            source_url=f"https://www.parkrun.org.uk/okura-kaigan-koen-{tag}/results/1/",
        )
    )
    db_session.flush()

    result = service.run_catalog_sync(factory, payload=payload(features), now=NOW)

    db_session.expire_all()
    healed = db_session.get(Location, location.id)
    assert healed.external_key == f"okurakaigankoen{tag}"
    assert (healed.latitude, healed.longitude) == (34.642379, 135.008025)
    assert healed.country == "Япония"
    assert healed.source_url == f"https://www.parkrun.jp/okurakaigankoen{tag}/"
    event = db_session.query(Event).filter(Event.location_id == location.id).one()
    assert event.source_url == f"https://www.parkrun.jp/okurakaigankoen{tag}/results/1/"
    assert result["heal"]["renamed"] == [f"okura-kaigan-koen-{tag} → okurakaigankoen{tag}"]
    assert any("Сайт поправил сам" in message for message in sent)


def test_catalog_store_falls_back_to_snapshot(db_session: Session, factory) -> None:
    # Каталог в базе пуст (фикстура его очистила) — работаем по снимку в репо.
    assert active_parkrun_slugs(db_session) is None
    snapshot = resolve_catalog_event(db_session, "okurakaigankoen")
    assert snapshot is not None and snapshot.page_url == "https://www.parkrun.jp/okurakaigankoen/"


def test_message_is_silent_without_news_and_compact_when_long(db_session: Session) -> None:
    assert notify.build_catalog_message(db_session, [], {}, active_total=3000, when=NOW) is None
    many = [
        {"kind": "added", "slug": f"place{i}", "name": f"Very Long Park Name Number {i}", "country_code": 97, "series_id": 1, "details": {}}
        for i in range(200)
    ]
    message = notify.build_catalog_message(db_session, many, {}, active_total=3000, when=NOW)
    assert message is not None and "➕ **Новые — 200**" in message and "🇬🇧 200" in message
    assert len(notify.to_telegram_html(message)) <= notify.MAX_HTML


def test_foreign_parkrun_page_is_not_russian_or_paused(db_session: Session, factory) -> None:
    from app.services.location_page_service import _identity_country, _identity_status

    tag = uuid4().hex[:6]
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    db_session.add(ParkrunEvent(slug=f"bushy{tag}", short_name="Bushy Park", country_code=97, is_active=True, source="live"))
    location = Location(platform_id=platform.id, external_key=f"bushy{tag}", name="Bushy Park", country="Великобритания")
    db_session.add(location)
    db_session.flush()
    db_session.info.pop("parkrun_active_slugs", None)

    class Index:
        db = db_session

    paused, cancelled = _identity_status(Index(), [(location, "parkrun")])
    assert (paused, cancelled) == (False, False)
    assert _identity_country([(location, "parkrun")]) == "Великобритания"


def test_admin_report_sends_html_with_plain_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import admin_telegram_notify

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(admin_telegram_notify, "send_admin_telegram_message", lambda text, html=False, **kw: calls.append((text, html)) or 1)
    monkeypatch.setattr(admin_telegram_notify, "_is_degraded", lambda: False)

    assert admin_telegram_notify.send_admin_report("**Каталог** [Bushy](https://www.parkrun.org.uk/bushy/)", markup=True)
    assert calls == [('<b>Каталог</b> <a href="https://www.parkrun.org.uk/bushy/">Bushy</a>', True)]


def test_parse_skips_malformed_items_and_rejects_bad_shapes() -> None:
    for bad in ([1, 2], {"events": [1], "countries": {}}, {"events": {"features": "x"}, "countries": {"97": {}}}):
        with pytest.raises(service.CatalogFetchError):
            service.parse_feed(json.dumps(bad).encode())
    nulls = feature(2, "nulls", "Nulls")
    nulls["geometry"]["coordinates"] = [None, None]
    feed = service.parse_feed(payload([feature(1, "good", "Good"), nulls, feature(3, "x" * 200, "Long"), "junk", {"properties": None}]))
    assert [event.slug for event in feed.events] == ["good"]


def test_unexpected_error_marks_run_failed_and_alerts(db_session: Session, factory, sent, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("value too long for type character varying(256)")

    monkeypatch.setattr(service, "apply_feed", boom)
    result = service.run_catalog_sync(factory, payload=payload(_base(uuid4().hex[:6])), now=NOW)

    assert result["status"] == "failed" and "RuntimeError" in result["errors"][0]
    assert db_session.query(ParkrunCatalogRun).filter(ParkrunCatalogRun.status == "running").count() == 0
    assert len(sent) == 1 and "не прошла" in sent[0]


def test_undelivered_summary_goes_out_with_next_run(db_session: Session, factory, sent, monkeypatch: pytest.MonkeyPatch) -> None:
    tag = uuid4().hex[:6]
    data = payload(_base(tag))
    monkeypatch.setattr("app.services.admin_telegram_notify.send_admin_report", lambda text, markup=False: False)
    assert service.run_catalog_sync(factory, payload=data, now=NOW)["status"] == "ok"

    delivered: list[str] = []
    monkeypatch.setattr(
        "app.services.admin_telegram_notify.send_admin_report",
        lambda text, markup=False: delivered.append(text) or True,
    )
    # Назавтра каталог тот же — прогон «unchanged», но вчерашняя сводка уходит.
    assert service.run_catalog_sync(factory, payload=data, now=NOW + timedelta(days=1))["status"] == "unchanged"
    assert len(delivered) == 1 and "➕ **Новые — 32**" in delivered[0]
    service.run_catalog_sync(factory, payload=data, now=NOW + timedelta(days=2))
    assert len(delivered) == 1


def test_heal_conflict_is_repeated_weekly_not_daily(db_session: Session, factory, sent, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import parkrun_catalog_heal as heal

    data = payload(_base(uuid4().hex[:6]))
    service.run_catalog_sync(factory, payload=data, now=NOW)
    calls = iter(range(100))
    monkeypatch.setattr(
        heal,
        "heal_parkrun_locations",
        lambda session_factory, **kwargs: heal.HealReport(conflicts=[f"слияние a → b: IntegrityError: key {next(calls)}"]),
    )
    sent.clear()
    for day in (1, 2, 3):
        service.run_catalog_sync(factory, payload=data, now=NOW + timedelta(days=day))
    assert sum("не смог поправить" in message for message in sent) == 1
    service.run_catalog_sync(factory, payload=data, now=NOW + timedelta(days=9))
    assert sum("не смог поправить" in message for message in sent) == 2


def test_chain_rename_in_one_feed(db_session: Session, factory, sent) -> None:
    """parkrun в одном ответе: alpha → слаг beta, beta → gamma. Живая строка не теряется."""
    tag = uuid4().hex[:6]
    base = _base(tag)
    service.run_catalog_sync(factory, payload=payload(base), now=NOW)
    changed = list(base)
    changed[0] = feature(9_000_001, f"beta{tag}", f"Alpha {tag}")
    changed[1] = feature(9_000_002, f"gamma{tag}", f"Beta {tag}")

    result = service.run_catalog_sync(factory, payload=payload(changed), now=NOW + timedelta(days=1))

    assert result["status"] == "ok" and result["catalog_renamed"] == 2
    slugs = dict(
        db_session.query(ParkrunEvent.parkrun_id, ParkrunEvent.slug)
        .filter(ParkrunEvent.parkrun_id.in_([9_000_001, 9_000_002]))
        .all()
    )
    assert slugs == {9_000_001: f"beta{tag}", 9_000_002: f"gamma{tag}"}
    renamed = [
        change.details
        for change in db_session.query(ParkrunEventChange).filter(ParkrunEventChange.kind == "renamed").all()
    ]
    assert sorted(item["old_slug"] for item in renamed) == [f"alpha{tag}", f"beta{tag}"]


def test_live_rename_marks_previous_name_old(db_session: Session, factory, sent) -> None:
    tag = uuid4().hex[:6]
    base = _base(tag)
    service.run_catalog_sync(factory, payload=payload(base), now=NOW)
    changed = list(base)
    changed[0] = feature(9_000_001, f"mortimer{tag}", f"Mortimer {tag}")

    service.run_catalog_sync(factory, payload=payload(changed), now=NOW + timedelta(days=1))

    kind = db_session.execute(
        text("select n.kind from parkrun_event_names n join parkrun_events e on e.id = n.event_id where e.slug = :slug and n.name_norm = :name"),
        {"slug": f"mortimer{tag}", "name": f"alpha{tag}"},
    ).scalar_one()
    assert kind == "old"
    view = key_plan.database_view(db_session)
    assert view.new_name(f"mortimer{tag}", f"Alpha {tag}") == f"Mortimer {tag}"


def test_heal_refuses_oversized_plan(db_session: Session, factory, sent, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import parkrun_catalog_heal as heal

    tag = uuid4().hex[:6]
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one()
    db_session.add(Location(platform_id=platform.id, external_key=f"alpha-{tag}", name=f"Alpha {tag}"))
    db_session.flush()
    monkeypatch.setattr(heal, "MAX_RENAMES", 0)

    result = service.run_catalog_sync(factory, payload=payload(_base(tag)), now=NOW)

    assert result["heal"]["renamed"] == []
    assert any("план больше обычного" in item for item in result["heal"]["conflicts"])
    assert db_session.query(Location).filter(Location.external_key == f"alpha-{tag}").count() == 1


def test_run_lock_lives_on_its_own_connection() -> None:
    """Блокировка прогона не уезжает с соединением сессии в пул."""
    from app.db.session import get_engine

    engine = get_engine()
    lock = text("select pg_try_advisory_lock(:key)")
    db = Session(bind=engine)
    try:
        with service._run_lock(db) as locked:
            assert locked
            db.execute(text("select 1"))
            db.commit()  # соединение сессии ушло в пул — блокировка на месте
            with engine.connect() as other:
                assert other.execute(lock, {"key": service.LOCK_KEY}).scalar() is False
        with engine.connect() as other:
            assert other.execute(lock, {"key": service.LOCK_KEY}).scalar() is True
            other.execute(text("select pg_advisory_unlock(:key)"), {"key": service.LOCK_KEY})
    finally:
        db.close()
