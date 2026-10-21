"""Отмены стартов по выбранным местам: подсказки, сохранение и фильтр рассылки."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Location, NotificationDelivery, Platform, User
from app.services import cancellation_notification_service as cancellations
from app.services import cancellation_places_service as places
from app.services import notification_channels_service as channels
from app.services import notification_service as notify
from app.services import site_search_service as search
from app.services.location_cancellation_notify import CancellationChange
from app.services.notification_channels_service import CheckOutcome


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    queued: list[UUID] = []
    monkeypatch.setattr(notify, "enqueue_delivery", lambda delivery_id: queued.append(delivery_id) or True)
    monkeypatch.setattr(cancellations, "still_ahead", lambda db, changes, now: changes)
    return queued


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        app_secret_key="test-secret-key",
        app_base_url="https://run5k.test",
        telegram_bot_token="t",
        telegram_bot_username="test_bot",
    )
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(channels, "get_settings", lambda: settings)
    monkeypatch.setattr(cancellations, "get_settings", lambda: settings)
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=True))
    return settings


def _entry(slug: str, name: str, *, key: str, city: str, region: str, country: str = "Россия") -> dict:
    return {
        "slug": slug,
        "identity_key": key,
        "name": name,
        "city": city,
        "region": region,
        "country": country,
        "platform_codes": ["five_verst"],
        "events_count": 10,
        "finishers_total": 100,
    }


@pytest.fixture()
def catalog(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Каталог локаций вместо Redis: три московских парка, Химки и Минск."""
    entries = [
        _entry("sokolniki", "Сокольники", key="catalog:sokol", city="Москва", region="Москва"),
        _entry("kuzminki", "Кузьминки", key="catalog:kuzm", city="Москва", region="Москва"),
        _entry("khimki", "Химки", key="catalog:khimki", city="Химки", region="Московская"),
        _entry("piter-park", "Удельный", key="catalog:udel", city="Санкт-Петербург", region="Санкт-Петербург"),
        _entry("minsk", "Минск Победителей", key="catalog:minsk", city="Минск", region="Минская", country="Беларусь"),
    ]
    docs = search._build_location_docs(entries)
    monkeypatch.setattr(search, "_location_docs", lambda db: docs)
    return entries


def _platform(db: Session, code: str = "five_verst") -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code, base_url=f"https://{code}.test", is_active=True)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, *, slug: str, name: str, city: str | None, region: str | None) -> Location:
    location = Location(platform_id=_platform(db).id, external_key=slug, name=name, city=city, region=region)
    db.add(location)
    db.flush()
    return location


def _user(db: Session, chat_id: int) -> User:
    user = User(display_name=f"Подписчик {chat_id}", telegram_id=chat_id, telegram_chat_id=chat_id)
    db.add(user)
    db.commit()
    notify.set_channel_enabled(db, user, "telegram", True)
    db.commit()
    return user


def _change(location: Location) -> CancellationChange:
    return CancellationChange(
        platform_code="five_verst", slug=location.external_key, name=location.name, cancelled=True
    )


# ---------------------------------------------------------------------------
# Подсказки


def test_suggest_puts_whole_places_before_locations(db_session: Session, catalog: list[dict]) -> None:
    items = places.suggest(db_session, "моск")
    kinds = [item["kind"] for item in items]
    # Сначала места целиком, затем отдельные локации. Москва в каталоге и
    # город, и регион — подсказка одна, регионом (он шире), но «город».
    assert kinds[: kinds.index("location")] == ["city", "region"]
    city = items[0]
    assert city["key"] == "region:москва" and city["label"] == "Москва"
    assert city["hint"].startswith("город · 2 локации")
    assert items[1]["label"] == "Московская область"


def test_suggest_understands_aliases_and_locations(db_session: Session, catalog: list[dict]) -> None:
    assert places.suggest(db_session, "спб")[0]["key"] == "region:санкт-петербург"
    assert places.suggest(db_session, "Беларусь")[0] == {
        "key": "country:беларусь",
        "kind": "country",
        "label": "Беларусь",
        "hint": "страна · 1 локация",
    }
    found = places.suggest(db_session, "сокол")
    assert found[0]["key"] == "catalog:sokol" and found[0]["kind"] == "location"
    assert places.suggest(db_session, "с") == []


# ---------------------------------------------------------------------------
# Сохранение


def test_places_are_saved_in_order_and_replaced(db_session: Session, catalog: list[dict]) -> None:
    user = _user(db_session, 9101)
    notify.update_prefs(db_session, user.id, cancellation_places=["region:москва", "catalog:udel", "region:москва"])
    db_session.commit()
    state = notify.settings_state(db_session, user)
    assert [(p["key"], p["kind"], p["label"]) for p in state["cancellation_places"]] == [
        ("region:москва", "city", "Москва"),
        ("catalog:udel", "location", "Удельный"),
    ]

    notify.update_prefs(db_session, user.id, cancellation_places=["catalog:udel"])
    db_session.commit()
    assert [p["key"] for p in places.user_places(db_session, user.id)] == ["catalog:udel"]

    notify.update_prefs(db_session, user.id, cancellation_places=[])
    db_session.commit()
    assert places.user_places(db_session, user.id) == []


def test_federal_city_chip_without_catalog(db_session: Session, catalog: list[dict], monkeypatch) -> None:
    """Каталог протух — Москва в строке всё равно город, а не регион."""
    user = _user(db_session, 9104)
    notify.update_prefs(db_session, user.id, cancellation_places=["region:москва", "region:московская"])
    db_session.commit()
    monkeypatch.setattr(search, "_location_docs", lambda db: [])
    assert [p["kind"] for p in places.user_places(db_session, user.id)] == ["city", "region"]


def test_places_saved_together_keep_their_order(db_session: Session, catalog: list[dict]) -> None:
    """Несколько мест одним сохранением встают в том порядке, в каком пришли,
    а не по алфавиту; добавленное позже — в конец строки."""
    user = _user(db_session, 9105)
    notify.update_prefs(db_session, user.id, cancellation_places=["catalog:udel", "region:москва", "catalog:kuzm"])
    db_session.commit()
    assert [p["label"] for p in places.user_places(db_session, user.id)] == ["Удельный", "Москва", "Кузьминки"]

    notify.update_prefs(
        db_session, user.id, cancellation_places=["catalog:udel", "region:москва", "catalog:kuzm", "city:химки"]
    )
    db_session.commit()
    assert [p["label"] for p in places.user_places(db_session, user.id)] == [
        "Удельный",
        "Москва",
        "Кузьминки",
        "Химки",
    ]


def test_unknown_place_is_rejected(db_session: Session, catalog: list[dict]) -> None:
    user = _user(db_session, 9102)
    with pytest.raises(ValueError):
        notify.update_prefs(db_session, user.id, cancellation_places=["city:атлантида"])
    with pytest.raises(ValueError):
        notify.update_prefs(db_session, user.id, cancellation_places=["street:тверская"])
    with pytest.raises(ValueError):
        notify.update_prefs(
            db_session, user.id, cancellation_places=[f"city:{n}" for n in range(places.MAX_PLACES + 1)]
        )


def test_kept_place_survives_catalog_outage(db_session: Session, catalog: list[dict], monkeypatch) -> None:
    """Каталог в Redis протух — уже выбранные места пересохраняются без него,
    новые не принимаются."""
    user = _user(db_session, 9103)
    notify.update_prefs(db_session, user.id, cancellation_places=["region:москва", "catalog:udel"])
    db_session.commit()
    monkeypatch.setattr(search, "_location_docs", lambda db: [])
    notify.update_prefs(db_session, user.id, cancellation_places=["region:москва"])
    with pytest.raises(ValueError, match="Каталог"):
        notify.update_prefs(db_session, user.id, cancellation_places=["region:москва", "city:химки"])


# ---------------------------------------------------------------------------
# Рассылка


def test_notify_respects_places(db_session: Session, catalog: list[dict]) -> None:
    tag = uuid4().hex[:6]
    moscow = _location(db_session, slug=f"msk-{tag}", name="Московский парк", city="Москва", region="Москва")
    khimki = _location(db_session, slug=f"khimki-{tag}", name="Химки", city="Химки", region="Московская")
    far = _location(db_session, slug=f"vl-{tag}", name="Владивосток", city="Владивосток", region="Приморский")

    everywhere = _user(db_session, 9201)
    city = _user(db_session, 9202)
    region = _user(db_session, 9203)
    for user, chosen in ((city, ["region:москва"]), (region, ["region:московская"])):
        notify.update_prefs(db_session, user.id, cancellation_places=chosen)
    db_session.commit()

    sent = cancellations.notify_cancellation_subscribers(db_session, [_change(moscow), _change(khimki), _change(far)])
    assert sent >= 3
    texts = {
        row.user_id: row.payload["text"]
        for row in db_session.query(NotificationDelivery).filter(
            NotificationDelivery.kind == "cancellations",
            NotificationDelivery.user_id.in_([everywhere.id, city.id, region.id]),
        )
    }
    assert all(name in texts[everywhere.id] for name in ("Московский парк", "Химки", "Владивосток"))
    assert "Московский парк" in texts[city.id] and "Химки" not in texts[city.id]
    assert "Химки" in texts[region.id] and "Владивосток" not in texts[region.id]

    # Ничего из своего — молчим.
    assert cancellations.notify_cancellation_subscribers(db_session, [_change(far)]) == 1  # только «вся страна»


def test_location_key_comes_from_catalog_link(db_session: Session, catalog: list[dict], monkeypatch) -> None:
    """Выбранная локация узнаётся по узлу каталога, а не по слагу системы."""
    tag = uuid4().hex[:6]
    park = _location(db_session, slug=f"park-{tag}", name="Парк", city=None, region=None)
    monkeypatch.setattr(
        "app.services.location_catalog_service.catalog_ids_for_locations",
        lambda db, locations: {park.id: "sokol"},
    )
    keys = places.location_place_keys(db_session, [("five_verst", park.external_key)])
    # Город подтягивается из записи каталога, хотя в строке локации пусто.
    assert keys[("five_verst", park.external_key)] == {
        "catalog:sokol",
        "city:москва",
        "region:москва",
        "country:россия",
    }
    assert places.place_allowed({"catalog:sokol"}, keys[("five_verst", park.external_key)])
    assert not places.place_allowed({"city:химки"}, keys[("five_verst", park.external_key)])
    assert places.place_allowed(set(), None)
    assert not places.place_allowed({"city:москва"}, None)


def test_friday_summary_names_chosen_places(db_session: Session, catalog: list[dict]) -> None:
    tag = uuid4().hex[:6]
    cancelled = _location(db_session, slug=f"vl-{tag}", name="Владивосток", city="Владивосток", region="Приморский")
    cancelled.is_cancelled = True
    user = _user(db_session, 9301)
    notify.update_prefs(db_session, user.id, cancellation_places=["region:москва"])
    db_session.commit()

    cancellations.send_friday_summaries(db_session, now=datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    row = db_session.query(NotificationDelivery).filter_by(user_id=user.id, dedupe_key="cancel-friday:2026-09-26").one()
    # Отмена далеко — для этого человека отмен нет, и сказано, где смотрели.
    assert row.payload["title"] == "✅ Отмен на завтра, 26.09, нет"
    assert "По местам: Москва." in row.payload["text"]
