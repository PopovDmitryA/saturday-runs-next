"""Наблюдатель отмен s95: ставит отметку, снимает её и не выдумывает статус."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform
from app.s95.api_client import S95ApiLocation
from app.s95.errors import S95BanDetected
from app.services.location_cancellation_notify import CancellationChange
from app.sync.s95_cancellations import watch_s95_cancellations
from app.sync.s95_location_status import S95LocationStatus

CANCELLED = S95LocationStatus(is_paused=False, is_cancelled=True, cancel_reason="Отмена забега 29 августа")
CLOSED = S95LocationStatus(is_paused=True, is_cancelled=False, cancel_reason=None)


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def _entry(slug: str, active: bool) -> S95ApiLocation:
    return S95ApiLocation(
        domain="https://s95.ru",
        slug=slug,
        name="Иваново",
        town="Иваново",
        place="Центральная набережная",
        active=active,
    )


def _location(db_session: Session, platform: Platform, slug: str, **kwargs: object) -> Location:
    row = Location(
        platform_id=platform.id,
        external_key=slug,
        name="Иваново",
        source_url=f"https://s95.ru/events/{slug}",
        **kwargs,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _changes_for(notify, slug: str) -> list:
    """Изменения по нашей площадке.

    Фильтруем по слагу, потому что тесты идут на живой базе: в ней есть и
    настоящие площадки s95, и настоящая отмена. Глобальные счётчики прогона
    зависели бы от того, что сейчас творится в реестре.
    """
    if not notify.call_args:
        return []
    return [item for item in notify.call_args.args[0] if item.slug == slug]


def _run(db_session: Session, entries: list[S95ApiLocation], status: object):
    with (
        patch("app.sync.s95_cancellations.fetch_all_locations", return_value=entries),
        patch("app.sync.s95_cancellations.resolve_s95_location_status", side_effect=status),
        patch("app.sync.s95_cancellations.notify_cancellation_changes") as notify,
        patch("app.sync.s95_cancellations.flush_location_catalog_caches"),
        patch("app.sync.s95_cancellations.flush_location_page_caches"),
    ):
        result = watch_s95_cancellations(db_session)
    return result, notify


def test_watch_marks_cancellation_with_reason(db_session: Session, s95_platform: Platform) -> None:
    slug = f"cancel-{uuid4().hex[:8]}"
    row = _location(db_session, s95_platform, slug, is_paused=False, is_cancelled=False)

    result, notify = _run(db_session, [_entry(slug, active=False)], lambda entry: CANCELLED)

    db_session.refresh(row)
    assert result.cancellations_active == 1
    assert row.is_cancelled is True
    assert row.cancel_reason == "Отмена забега 29 августа"
    assert _changes_for(notify, slug) == [
        CancellationChange(
            platform_code="s95",
            slug=slug,
            name="Иваново",
            cancelled=True,
            reason="Отмена забега 29 августа",
        )
    ]


def test_watch_clears_cancellation_when_registry_is_back(
    db_session: Session, s95_platform: Platform
) -> None:
    slug = f"back-{uuid4().hex[:8]}"
    row = _location(
        db_session,
        s95_platform,
        slug,
        is_paused=False,
        is_cancelled=True,
        cancel_reason="Отмена забега 29 августа",
    )

    result, notify = _run(db_session, [_entry(slug, active=True)], lambda entry: None)

    db_session.refresh(row)
    assert row.is_cancelled is False
    assert row.cancel_reason is None
    assert [item.cancelled for item in _changes_for(notify, slug)] == [False]


def test_watch_keeps_pause_flag_alone(db_session: Session, s95_platform: Platform) -> None:
    """«Не действует» — вывод синка реестра и правила молчания, не наблюдателя.

    Иначе четыре прогона в сутки спорили бы со статусом, который эти двое
    только что выставили.
    """
    slug = f"paused-{uuid4().hex[:8]}"
    row = _location(db_session, s95_platform, slug, is_paused=True, is_cancelled=False)

    _run(db_session, [_entry(slug, active=False)], lambda entry: CLOSED)

    db_session.refresh(row)
    assert row.is_paused is True
    assert row.is_cancelled is False


def test_watch_does_not_clear_when_page_unreadable(
    db_session: Session, s95_platform: Platform
) -> None:
    """Страница не открылась — про эту площадку мы ничего не знаем и не трогаем её."""
    slug = f"unknown-{uuid4().hex[:8]}"
    row = _location(
        db_session,
        s95_platform,
        slug,
        is_paused=False,
        is_cancelled=True,
        cancel_reason="Отмена забега 29 августа",
    )

    def _boom(entry: S95ApiLocation) -> S95LocationStatus:
        raise RuntimeError("503")

    result, notify = _run(db_session, [_entry(slug, active=False)], _boom)

    db_session.refresh(row)
    assert row.is_cancelled is True
    assert row.cancel_reason == "Отмена забега 29 августа"
    assert any(slug in error for error in result.errors)
    assert _changes_for(notify, slug) == []


def test_watch_stops_on_ban_and_keeps_unchecked_rows(
    db_session: Session, s95_platform: Platform
) -> None:
    """Бан обрывает обход — и всё, до чего не дошли, остаётся как было.

    Иначе непроверенная площадка теряла бы отметку об отмене только потому,
    что забанили нас на предыдущей.
    """
    first = f"ban-{uuid4().hex[:8]}"
    second = f"ban-{uuid4().hex[:8]}"
    rows = [
        _location(db_session, s95_platform, slug, is_paused=False, is_cancelled=True)
        for slug in (first, second)
    ]

    def _banned(entry: S95ApiLocation) -> S95LocationStatus:
        raise S95BanDetected("HTTP 403")

    result, notify = _run(
        db_session,
        [_entry(first, active=False), _entry(second, active=False)],
        _banned,
    )

    for row in rows:
        db_session.refresh(row)
        assert row.is_cancelled is True
    assert result.banned is True
    assert _changes_for(notify, first) == []
    assert _changes_for(notify, second) == []


def test_watch_survives_broken_registry(db_session: Session, s95_platform: Platform) -> None:
    with patch("app.sync.s95_cancellations.fetch_all_locations", side_effect=RuntimeError("timeout")):
        result = watch_s95_cancellations(db_session)

    assert result.errors
    assert result.entries_total == 0


def test_watch_does_not_clear_on_partial_registry(
    db_session: Session, s95_platform: Platform
) -> None:
    """Реестр прочитан не целиком — «нет в списке» не означает «отмена снята».

    s95 закрылся от прода по IP (или лёг один из доменов): fetch_all_locations
    молча вернёт пустой/частичный список. Снимать по нему отметки нельзя —
    иначе каждый прогон слал бы ложные «✅ Отмена снята» при живой отмене.
    """
    slug = f"partial-{uuid4().hex[:8]}"
    row = _location(
        db_session,
        s95_platform,
        slug,
        is_paused=False,
        is_cancelled=True,
        cancel_reason="Отмена забега 29 августа",
    )

    def _partial_fetch(*, errors: list[str] | None = None) -> list[S95ApiLocation]:
        if errors is not None:
            errors.append("https://s95.ru: pages.json: connection refused")
        return []

    with (
        patch("app.sync.s95_cancellations.fetch_all_locations", side_effect=_partial_fetch),
        patch("app.sync.s95_cancellations.notify_cancellation_changes") as notify,
        patch("app.sync.s95_cancellations.flush_location_catalog_caches"),
        patch("app.sync.s95_cancellations.flush_location_page_caches"),
    ):
        result = watch_s95_cancellations(db_session)

    db_session.refresh(row)
    assert row.is_cancelled is True
    assert row.cancel_reason == "Отмена забега 29 августа"
    assert any("pages.json" in error for error in result.errors)
    assert _changes_for(notify, slug) == []


def test_watch_does_not_clear_on_empty_registry(
    db_session: Session, s95_platform: Platform
) -> None:
    """Пустой (но «успешный») реестр так же непригоден для снятия отметок."""
    slug = f"empty-{uuid4().hex[:8]}"
    row = _location(
        db_session,
        s95_platform,
        slug,
        is_paused=False,
        is_cancelled=True,
        cancel_reason="Отмена забега 29 августа",
    )

    result, notify = _run(db_session, [], lambda entry: None)

    db_session.refresh(row)
    assert row.is_cancelled is True
    assert _changes_for(notify, slug) == []
