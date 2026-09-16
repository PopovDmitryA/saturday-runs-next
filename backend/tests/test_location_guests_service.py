"""Гости локации: кто на старте приехал не из дома.

Заявка из бэклога сайта («В протокол локации добавить количество "туристов"»):
столбцов «финишёры / волонтёры / новички» мало, нужен ещё один — сколько людей
приехало с другой домашней площадки. От колонки «Впервые здесь» число
отличается тем, что гость мог бывать здесь и раньше: важно не «впервые», а
«дом в другом месте». Слово «гость» — то же, что в рубрике поста организатора
«🧳 Гости локации», и дом считается той же функцией.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunResult
from app.services.location_guests_service import (
    _compute_guests_by_event,
    location_guests_summary,
)
from app.services.location_page_service import resolve_location_identity


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _location(db_session: Session, name: str) -> Location:
    row = Location(
        platform_id=_platform(db_session, "five_verst").id,
        external_key=f"guests-{uuid4().hex[:8]}",
        name=name,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _participant(db_session: Session, name: str) -> Participant:
    row = Participant(
        platform_id=_platform(db_session, "five_verst").id,
        external_user_id=f"77{uuid4().int % 10**9:09d}",
        display_name=name,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _event(db_session: Session, location: Location, event_date: date) -> Event:
    row = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{event_date.isoformat()}",
        event_date=event_date,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _finish(db_session: Session, event: Event, participant: Participant) -> None:
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"run-{uuid4()}",
            finish_time_sec=1500,
            finish_time_display="00:25:00",
            status="finished",
        )
    )
    db_session.flush()


@pytest.fixture
def scene(db_session: Session) -> dict[str, object]:
    """Своя площадка и соседняя; трое людей с разными «домами»."""
    home = _location(db_session, "Наш парк")
    away = _location(db_session, "Соседний парк")

    local = _participant(db_session, "Местный Житель")
    guest = _participant(db_session, "Заезжий Гость")
    newcomer = _participant(db_session, "Первый Раз")

    our_days = [date(2026, 5, 2), date(2026, 5, 9), date(2026, 5, 16)]
    our_events = [_event(db_session, home, day) for day in our_days]
    away_events = [_event(db_session, away, day) for day in (date(2026, 4, 4), date(2026, 4, 11))]

    # Местный бегает здесь всегда — это его дом.
    for event in our_events:
        _finish(db_session, event, local)
    # Гость живёт на соседней площадке (там два старта против одного здесь),
    # но приезжает к нам не впервые: он был и на первом, и на последнем старте.
    for event in away_events:
        _finish(db_session, event, guest)
    _finish(db_session, our_events[0], guest)
    _finish(db_session, our_events[-1], guest)
    # Дебютант вышел только у нас — дом у него здесь, гостем он не считается.
    _finish(db_session, our_events[-1], newcomer)
    db_session.commit()
    return {"home": home, "events": our_events}


def test_guest_is_counted_on_every_visit(db_session: Session, scene: dict[str, object]) -> None:
    """Гость попадает в счёт и на повторном приезде, а не только на первом."""
    home = scene["home"]
    events = scene["events"]
    identity = resolve_location_identity(db_session, home.external_key)  # type: ignore[union-attr]
    assert identity is not None

    by_event = _compute_guests_by_event(db_session, identity)
    assert by_event[events[0].id] == 1  # type: ignore[index]
    assert by_event[events[1].id] == 0  # type: ignore[index]
    # Второй приезд того же человека — снова гость, хотя «впервые здесь» он уже не был.
    assert by_event[events[2].id] == 1  # type: ignore[index]


def test_guests_cache_tops_up_new_events(db_session: Session, scene: dict[str, object]) -> None:
    """Догон нового старта даёт то же, что полный пересчёт истории.

    Полный проход по крупной площадке — секунды, и умножать их на 270 локаций
    в каждом прогреве незачем: кэш дополняется одним новым стартом.
    """
    from app.services.location_guests_service import _write_cache, location_guests_by_event

    identity = resolve_location_identity(db_session, scene["home"].external_key)  # type: ignore[union-attr]
    assert identity is not None
    full = _compute_guests_by_event(db_session, identity)

    # В кэше нет последнего старта — refresh обязан его дочитать.
    latest = max(full)
    _write_cache(identity.identity_key, {k: v for k, v in full.items() if k != latest})
    topped = location_guests_by_event(db_session, identity, refresh=True)
    assert topped == full

    # Без refresh кэш отдаётся как есть, без похода в базу.
    _write_cache(identity.identity_key, {k: v for k, v in full.items() if k != latest})
    assert latest not in location_guests_by_event(db_session, identity)


def test_guests_summary_shares(db_session: Session, scene: dict[str, object]) -> None:
    """Сводка страницы: всего, доля от финишей и в среднем на старте."""
    identity = resolve_location_identity(db_session, scene["home"].external_key)  # type: ignore[union-attr]
    assert identity is not None

    _by_event, summary = location_guests_summary(db_session, identity)
    # Финишей у нас шесть: местный ×3, гость ×2, дебютант ×1.
    assert summary["total"] == 2
    assert summary["share_pct"] == pytest.approx(33.3)
    assert summary["avg_per_event"] == pytest.approx(0.7)
