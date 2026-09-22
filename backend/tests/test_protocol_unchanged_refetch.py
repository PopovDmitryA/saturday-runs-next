"""Перечитка неизменного протокола 5 вёрст — не перезапись (SYNC-5V-02).

До 21.09.2026 хеш протокола считался по сырому HTML и «менялся» почти на
каждой перечитке: страница отдаёт nonce и метки времени. Каждая перечитка
переписывала все строки (UPDATE на строку), подрезала кэши локации и
запускала прогревы дашбордов. Теперь хеш строится по разобранным строкам, и
совпавший протокол только двигает отметки проверки.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import fakeredis
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from app.models import Event, Location, Platform, ProtocolSyncState, RunResult
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.services.location_freshness import STALE_AFTER_WRITE_SECONDS
from app.services.location_page_service import location_page_cache_key
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
from app.sync.protocol_content_hash import protocol_content_hash

DAY = date(2027, 4, 10)


def _platform(db: Session) -> Platform:
    row = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def _location(db: Session, platform: Platform) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"unchanged-{uuid4().hex[:8]}",
        name="Unchanged Park",
        source_url="https://example.test/unchanged/",
    )
    db.add(location)
    db.flush()
    return location


def _runs(slug: str, count: int, *, shift_sec: int = 0) -> list[CanonicalRunResult]:
    return [
        CanonicalRunResult(
            external_result_key=f"{slug}:{DAY.isoformat()}:{910000 + i}",
            event_date=DAY,
            external_user_id=str(910000 + i),
            participant_name=f"Runner {i}",
            position=i + 1,
            finish_time_sec=1200 + i + shift_sec,
            finish_time_display="00:20:00",
            age_category="М30-34",
            club_name="Club",
            achievement_labels=["Первый финиш"] if i == 0 else [],
            location_external_key=slug,
            event_number=7,
        )
        for i in range(count)
    ]


def _vols(slug: str, count: int) -> list[CanonicalVolunteerResult]:
    return [
        CanonicalVolunteerResult(
            external_result_key=f"{slug}:{DAY.isoformat()}:vol:{810000 + i}:marshal",
            event_date=DAY,
            external_user_id=str(810000 + i),
            participant_name=f"Vol {i}",
            role="Маршал",
            location_external_key=slug,
            event_number=7,
        )
        for i in range(count)
    ]


def _summary(slug: str, *, finishers: int, volunteers: int, hash_: str = "sum-1") -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=f"{slug}:7:{DAY.isoformat()}",
        event_date=DAY,
        event_number=7,
        location_external_key=slug,
        location_name="Unchanged Park",
        finishers_count=finishers,
        volunteers_count=volunteers,
        source_url=f"https://5verst.ru/{slug}/results/{DAY.strftime('%d.%m.%Y')}/",
        summary_hash=hash_,
    )


class _SqlCounter:
    """Считает SQL-операторы по типу и таблице через before_cursor_execute."""

    def __init__(self, db: Session) -> None:
        self.counts: Counter[str] = Counter()
        self._target = db.connection()

    def _listen(self, conn, cursor, statement, parameters, context, executemany) -> None:
        head = statement.lstrip().split(None, 3)
        if not head:
            return
        verb = head[0].upper()
        if verb == "UPDATE" and len(head) > 1:
            self.counts[f"UPDATE {head[1]}"] += 1
        self.counts[verb] += 1

    def __enter__(self) -> _SqlCounter:
        sa_event.listen(self._target, "before_cursor_execute", self._listen)
        return self

    def __exit__(self, *exc: object) -> None:
        sa_event.remove(self._target, "before_cursor_execute", self._listen)


def _ingest(db: Session, platform: Platform, location: Location, summary, runs, vols, *, html: str = "<html/>"):
    summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
    with patch(
        "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
        return_value=(runs, vols, html),
    ):
        result = fetch_and_upsert_event_protocol(db, platform, location, summary, summary_row)
    db.commit()
    return result


def _state(db: Session, summary) -> ProtocolSyncState:
    event = db.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    return db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()


# --- хеш ----------------------------------------------------------------------


def test_content_hash_ignores_row_order_and_html() -> None:
    runs = _runs("h", 3)
    vols = _vols("h", 2)
    assert protocol_content_hash(runs, vols) == protocol_content_hash(list(reversed(runs)), list(reversed(vols)))


def test_content_hash_sees_time_and_role_changes() -> None:
    base = protocol_content_hash(_runs("h", 3), _vols("h", 2))
    assert protocol_content_hash(_runs("h", 3, shift_sec=1), _vols("h", 2)) != base
    other_role = _vols("h", 2)
    other_role[0].role = "Организатор"
    assert protocol_content_hash(_runs("h", 3), other_role) != base
    assert protocol_content_hash(_runs("h", 2), _vols("h", 2)) != base


def test_content_hash_survives_none_fields() -> None:
    """Позиция и время бывают None — сортировка по кортежам на этом падала."""
    runs = _runs("h", 2)
    runs[0].position = None
    runs[1].finish_time_sec = None
    assert len(protocol_content_hash(runs, [])) == 64


# --- перечитка ----------------------------------------------------------------


def test_unchanged_protocol_is_not_rewritten(db_session: Session, fake_redis: fakeredis.FakeRedis) -> None:
    platform = _platform(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    summary = _summary(slug, finishers=5, volunteers=2)

    first = _ingest(db_session, platform, location, summary, _runs(slug, 5), _vols(slug, 2), html="<html>a</html>")
    assert first.run_results_upserted == 5
    assert first.volunteer_results_upserted == 2

    state = _state(db_session, summary)
    fetched_before = state.last_protocol_fetched_at
    # Снимок витрины локации с полным TTL: неизменный протокол его подрезать не должен.
    page_key = location_page_cache_key(slug)
    fake_redis.setex(page_key, 3 * 60 * 60, "{}")
    # Отметки проверки должны сдвинуться вперёд — отматываем их назад.
    state.last_protocol_check_at = datetime.now(timezone.utc) - timedelta(days=3)
    state.last_protocol_fetched_at = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.commit()

    # Тот же протокол, другой HTML (nonce/метки времени) — по-старому это была
    # бы полная перезапись.
    with _SqlCounter(db_session) as counter:
        second = _ingest(db_session, platform, location, summary, _runs(slug, 5), _vols(slug, 2), html="<html>b</html>")

    assert second.run_results_upserted == 0
    assert second.volunteer_results_upserted == 0
    assert second.protocol_changed is False
    assert second.run_results_count == 5
    assert second.volunteer_results_count == 2
    assert counter.counts["UPDATE run_results"] == 0
    assert counter.counts["UPDATE volunteer_results"] == 0
    assert counter.counts["INSERT"] == 0
    assert counter.counts["DELETE"] == 0

    db_session.refresh(state)
    assert state.last_protocol_check_at > datetime.now(timezone.utc) - timedelta(minutes=1)
    assert state.last_protocol_fetched_at > datetime.now(timezone.utc) - timedelta(minutes=1)
    assert fetched_before is not None
    assert state.protocol_source_hash == first.protocol_source_hash
    assert fake_redis.ttl(page_key) > STALE_AFTER_WRITE_SECONDS


def test_unchanged_protocol_still_settles_summary_debt(db_session: Session) -> None:
    """Саммари поменялось (число волонтёров), строки — нет: долг закрывается без перезаписи."""
    from app.sync.protocol_debt import protocol_is_stale

    platform = _platform(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    _ingest(db_session, platform, location, _summary(slug, finishers=3, volunteers=1), _runs(slug, 3), _vols(slug, 1))

    moved = _summary(slug, finishers=3, volunteers=1, hash_="sum-2")
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, moved)
    db_session.commit()
    state = _state(db_session, moved)
    assert protocol_is_stale(state, summary_row)

    with _SqlCounter(db_session) as counter:
        result = _ingest(db_session, platform, location, moved, _runs(slug, 3), _vols(slug, 1))

    assert result.run_results_upserted == 0
    assert counter.counts["UPDATE run_results"] == 0
    db_session.refresh(state)
    db_session.refresh(summary_row)
    assert state.summary_hash_at_fetch == "sum-2"
    assert not protocol_is_stale(state, summary_row)


def test_changed_protocol_is_rewritten(db_session: Session) -> None:
    platform = _platform(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    summary = _summary(slug, finishers=4, volunteers=1)
    _ingest(db_session, platform, location, summary, _runs(slug, 4), _vols(slug, 1))

    with _SqlCounter(db_session) as counter:
        result = _ingest(db_session, platform, location, summary, _runs(slug, 4, shift_sec=5), _vols(slug, 1))

    assert result.protocol_changed is True
    assert result.run_results_upserted == 4
    assert counter.counts["UPDATE run_results"] >= 4
    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    times = sorted(
        row.finish_time_sec for row in db_session.query(RunResult).filter(RunResult.event_id == event.id).all()
    )
    assert times == [1205, 1206, 1207, 1208]


def test_rows_missing_in_db_force_rewrite_despite_same_hash(db_session: Session) -> None:
    """Хеш совпал, но строк в базе меньше, чем при прошлом фетче, — переписываем."""
    platform = _platform(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    summary = _summary(slug, finishers=3, volunteers=0)
    _ingest(db_session, platform, location, summary, _runs(slug, 3), [])

    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    victim = db_session.query(RunResult).filter(RunResult.event_id == event.id).first()
    db_session.delete(victim)
    db_session.commit()

    result = _ingest(db_session, platform, location, summary, _runs(slug, 3), [])
    assert result.run_results_upserted == 3
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).count() == 3


def test_legacy_html_hash_triggers_one_full_pass(db_session: Session) -> None:
    """После выката в базе хеш по HTML: первая перечитка идёт по полному пути и меняет формат."""
    platform = _platform(db_session)
    location = _location(db_session, platform)
    slug = location.external_key
    summary = _summary(slug, finishers=2, volunteers=0)
    first = _ingest(db_session, platform, location, summary, _runs(slug, 2), [])

    state = _state(db_session, summary)
    state.protocol_source_hash = "0" * 64  # хеш старого формата
    db_session.commit()

    second = _ingest(db_session, platform, location, summary, _runs(slug, 2), [])
    assert second.run_results_upserted == 2
    assert second.protocol_changed is True
    db_session.refresh(state)
    assert state.protocol_source_hash == first.protocol_source_hash

    third = _ingest(db_session, platform, location, summary, _runs(slug, 2), [])
    assert third.run_results_upserted == 0
    assert third.protocol_changed is False
