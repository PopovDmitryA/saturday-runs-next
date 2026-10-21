"""Статус локации по системам в каталоге — для фильтра «5 вёрст» / «С95».

Репорт 22.10.2026: при фильтре «5 вёрст» каталог насчитывал 217 активных
вместо 216. Раменское ушло из 5 вёрст в С95, а статус у локации один на все
системы — и она шла в активные 5 вёрст.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Location, LocationCatalog, LocationCatalogLink, Platform
from app.schemas.locations import LocationsIndexResponse
from app.services.location_page_service import _compute_locations_index, _identity_status_by_system

ACTIVE = {"is_paused": False, "is_cancelled": False}
PAUSED = {"is_paused": True, "is_cancelled": False}
CANCELLED = {"is_paused": False, "is_cancelled": True}


def _row(code: str, *, paused: bool = False, cancelled: bool = False, key: str = "x") -> tuple[Location, str]:
    # Правилу нужны только флаги строки и её ключ — сама Location не нужна.
    return (SimpleNamespace(is_paused=paused, is_cancelled=cancelled, external_key=key), code)  # type: ignore[return-value]


def test_single_system_keeps_the_identity_status() -> None:
    """Одна система — общий статус как есть, даже если флаги строки другие:
    обычные локации от правки не меняются."""
    catalog_index = SimpleNamespace(db=None)
    assert _identity_status_by_system(catalog_index, [_row("five_verst", paused=True)], (False, False)) == {  # type: ignore[arg-type]
        "five_verst": ACTIVE
    }
    assert _identity_status_by_system(catalog_index, [_row("s95")], (True, False)) == {"s95": PAUSED}  # type: ignore[arg-type]


def test_moved_location_is_paused_only_in_the_old_system() -> None:
    """Раменское: в С95 действует, в 5 вёрст на паузе — общий статус «действует»."""
    status = _identity_status_by_system(
        SimpleNamespace(db=None),  # type: ignore[arg-type]
        [_row("s95"), _row("five_verst", paused=True)],
        (False, False),
    )
    assert status == {"five_verst": PAUSED, "s95": ACTIVE}


def test_whole_identity_paused_or_cancelled_spreads_to_every_system() -> None:
    rows = [_row("s95"), _row("five_verst")]
    catalog_index = SimpleNamespace(db=None)
    assert _identity_status_by_system(catalog_index, rows, (True, False)) == {"five_verst": PAUSED, "s95": PAUSED}  # type: ignore[arg-type]
    assert _identity_status_by_system(catalog_index, rows, (False, True)) == {  # type: ignore[arg-type]
        "five_verst": CANCELLED,
        "s95": CANCELLED,
    }


def test_cancellation_and_best_row_within_a_system() -> None:
    """Отмена ближайшего старта — своя у системы; из двух строк одной системы
    берётся лучшая (действующая важнее паузы)."""
    status = _identity_status_by_system(
        SimpleNamespace(db=None),  # type: ignore[arg-type]
        [
            _row("five_verst", paused=True, key="old"),
            _row("five_verst", key="new"),
            _row("s95", cancelled=True),
        ],
        (False, False),
    )
    assert status == {"five_verst": ACTIVE, "s95": CANCELLED}


def test_parkrun_member_follows_the_parkrun_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """parkrun — по действующему каталогу parkrun, как и в общем статусе: флаг
    строки у российского parkrun после 2022 года ничего не говорит."""
    import app.parkrun.catalog_store as catalog_store

    monkeypatch.setattr(catalog_store, "active_parkrun_slugs", lambda _db: {"livepark"})
    catalog_index = SimpleNamespace(db=None)
    rows = [_row("five_verst"), _row("parkrun", key="deadpark")]
    assert _identity_status_by_system(catalog_index, rows, (False, False))["parkrun"] == PAUSED  # type: ignore[arg-type]
    rows = [_row("five_verst"), _row("parkrun", key="livepark")]
    assert _identity_status_by_system(catalog_index, rows, (False, False))["parkrun"] == ACTIVE  # type: ignore[arg-type]


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _location(db_session: Session, code: str, slug: str, *, paused: bool, dates: list[date]) -> Location:
    platform = _platform(db_session, code)
    location = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Тестовое Раменское",
        city="Тестоград",
        country="Россия",
        latitude=55.57,
        longitude=38.23,
        is_official_map=True,
        is_paused=paused,
        registry_paused=paused,
    )
    db_session.add(location)
    db_session.flush()
    for index, event_date in enumerate(dates):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:{index}",
            event_date=event_date,
            event_number=index + 1,
            title=f"{slug} #{index + 1}",
        )
        db_session.add(event)
        db_session.flush()
        # «Строка со стартами» каталог узнаёт по сводкам протоколов: без
        # сводки обе строки считались бы пустыми, и узел ушёл бы в паузу
        # целиком.
        db_session.add(
            EventSummary(
                platform_id=platform.id,
                location_id=location.id,
                event_id=event.id,
                external_event_key=event.external_event_key,
                event_date=event_date,
                event_number=index + 1,
                summary_hash=f"{slug}-{index}",
            )
        )
    db_session.flush()
    return location


def test_index_row_of_a_moved_location(db_session: Session) -> None:
    """Сквозь сборку каталога: общий статус «действует», в 5 вёрст — пауза,
    в С95 — действует; ответ ложится в схему эндпоинта."""
    suffix = uuid4().hex[:8]
    old = _location(db_session, "five_verst", f"sys-status-5v-{suffix}", paused=True, dates=[date(2026, 9, 19)])
    new = _location(db_session, "s95", f"sys-status-s95-{suffix}", paused=False, dates=[date(2026, 10, 17)])
    catalog = LocationCatalog(canonical_name="Тестовое Раменское", active_platform="s95")
    db_session.add(catalog)
    db_session.flush()
    for location, code in ((old, "five_verst"), (new, "s95")):
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=_platform(db_session, code).id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db_session.commit()

    payload = _compute_locations_index(db_session)
    row = next(item for item in payload["items"] if item["identity_key"] == f"catalog:{catalog.id}")  # type: ignore[index,union-attr]
    assert row["is_paused"] is False
    assert row["is_cancelled"] is False
    assert row["system_status"] == {"five_verst": PAUSED, "s95": ACTIVE}
    LocationsIndexResponse.model_validate(payload)
