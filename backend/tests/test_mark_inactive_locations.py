from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform
from app.sync.mark_inactive_locations_paused import mark_inactive_locations_paused


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def test_mark_inactive_locations_paused_skips_upcoming(
    db_session: Session, s95_platform: Platform
) -> None:
    """Площадку, которую реестр пометил «скоро», правило молчания не трогает:
    там заявление системы, оно сильнее догадки по датам."""
    slug = f"inactive-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Inactive Upcoming",
        is_cancelled=False,
        is_paused=False,
        is_upcoming=True,
        latitude=55.75,
        longitude=37.62,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:old",
            event_date=date.today() - timedelta(days=200),
            event_number=1,
            summary_hash="abc",
        )
    )
    db_session.commit()

    mark_inactive_locations_paused(db_session, inactive_days=100, platform_codes=("s95",))
    db_session.refresh(location)
    assert location.is_paused is False


def test_mark_inactive_locations_paused_marks_stale_events(db_session: Session, s95_platform: Platform) -> None:
    slug = f"inactive-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Inactive Active",
        is_cancelled=False,
        is_paused=False,
        latitude=55.75,
        longitude=37.62,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:old",
            event_date=date.today() - timedelta(days=200),
            event_number=1,
            summary_hash="abc",
        )
    )
    db_session.commit()

    mark_inactive_locations_paused(db_session, inactive_days=100, platform_codes=("s95",))
    db_session.refresh(location)
    assert location.is_paused is True


def test_mark_inactive_locations_revives_returned_location(
    db_session: Session, s95_platform: Platform
) -> None:
    """Вернувшаяся площадка снимает статус сама.

    Бузулук зимовал 126 дней и вернулся: статус считается от дат, а не ставится
    навсегда, иначе сезонная площадка так и висела бы «не действует» (решение
    Дмитрия 20.08.2026).
    """
    slug = f"revived-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Returned",
        is_cancelled=False,
        is_paused=True,
        latitude=55.75,
        longitude=37.62,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:fresh",
            event_date=date.today() - timedelta(days=7),
            event_number=2,
            summary_hash="fresh",
        )
    )
    db_session.commit()

    result = mark_inactive_locations_paused(
        db_session, inactive_days=100, platform_codes=("s95",)
    )
    db_session.refresh(location)
    assert location.is_paused is False
    assert result.locations_revived == 1


def test_mark_inactive_locations_skips_roving_series(
    db_session: Session, s95_platform: Platform
) -> None:
    """Разъездную серию без координат правило не трогает.

    «С95 и друзья» и «S95 & Friends» — не место, а формат: собираются
    нерегулярно, и «не действует» после ста тихих дней было бы про них неправдой.
    На проде 27.08.2026 это были ровно те две строки, которые правило и хотело
    пометить.
    """
    slug = f"roving-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="С95 и друзья",
        is_cancelled=False,
        is_paused=False,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:old",
            event_date=date.today() - timedelta(days=334),
            event_number=1,
            summary_hash="roving",
        )
    )
    db_session.commit()

    result = mark_inactive_locations_paused(
        db_session, inactive_days=100, platform_codes=("s95",)
    )

    db_session.refresh(location)
    assert location.is_paused is False
    assert result.locations_skipped_no_coords >= 1


def _platform(db_session: Session, code: str) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        pytest.skip(f"{code} platform not seeded")
    return row


def test_runpark_silence_counted_from_events(db_session: Session) -> None:
    """У RunPark нет сводок стартов: последний старт берётся из протоколов.

    Репорт 15.10.2026: Ньюпор Run 5К (последний старт 18.05.2025) висел
    «действующим» — правило читало только event_summaries и пропускало все
    51 локацию RunPark молча.
    """
    from app.models import Event

    runpark = _platform(db_session, "runpark")
    slug = f"rp-silent-{uuid4().hex[:8]}"
    location = Location(
        platform_id=runpark.id,
        external_key=slug,
        name="Ньюпор тест",
        is_paused=False,
        latitude=56.3,
        longitude=43.9,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add_all(
        [
            Event(
                platform_id=runpark.id,
                location_id=location.id,
                external_event_key=f"{slug}:old",
                event_date=date.today() - timedelta(days=400),
                event_number=1,
                title="Старт",
            ),
            # Тестовый старт не считается: по нему площадка выглядела бы живой.
            Event(
                platform_id=runpark.id,
                location_id=location.id,
                external_event_key=f"{slug}:test",
                event_date=date.today() - timedelta(days=3),
                event_number=2,
                title="Тест",
                is_test_event=True,
            ),
        ]
    )
    db_session.commit()

    result = mark_inactive_locations_paused(db_session, inactive_days=100, platform_codes=("runpark",))
    db_session.refresh(location)
    assert location.is_paused is True
    assert result.locations_paused >= 1


def test_status_override_beats_dates_and_registry(db_session: Session, s95_platform: Platform) -> None:
    """Ручной статус админа сильнее и правила молчания, и реестра системы.

    Томск Сосновый Бор переехал в Михайловскую рощу: свежий старт был три
    недели назад, реестр паузы не ставит — без ручного статуса «не действует»
    наступило бы только через три месяца (репорт 15.10.2026).
    """
    from app.sync.location_registry_status import apply_location_registry_flags

    slug = f"override-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Переехавшая",
        is_paused=False,
        status_override="inactive",
        latitude=56.48,
        longitude=84.95,
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:fresh",
            event_date=date.today() - timedelta(days=19),
            event_number=1,
            summary_hash="fresh",
        )
    )
    db_session.commit()

    mark_inactive_locations_paused(db_session, location_ids=[location.id])
    db_session.refresh(location)
    assert location.is_paused is True

    # Реестр, не знающий о переезде, вернул бы «действует» — ручной статус держит.
    apply_location_registry_flags(location, is_paused=False, is_cancelled=False)
    assert location.is_paused is True

    # «Действует» вручную держит площадку живой и после ста тихих дней.
    location.status_override = "active"
    mark_inactive_locations_paused(db_session, location_ids=[location.id], as_of=date.today() + timedelta(days=200))
    assert location.is_paused is False

    # Ручной статус снят — снова считает правило.
    location.status_override = None
    mark_inactive_locations_paused(db_session, location_ids=[location.id], as_of=date.today() + timedelta(days=200))
    assert location.is_paused is True


def test_registry_does_not_undo_silence_pause_every_evening(db_session: Session, s95_platform: Platform) -> None:
    """Реестр без паузы не снимает паузу, поставленную правилом молчания.

    Раньше синк реестра в 20:50 писал is_paused=False всем «живым по реестру»,
    правило в 21:10 ставило паузу обратно, и двадцать минут каждый вечер
    молчащая площадка числилась «действующей» (замечено 15.10.2026).
    """
    from app.sync.location_registry_status import apply_location_registry_flags

    slug = f"silent-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id, external_key=slug, name="Молчащая", is_paused=False, latitude=55.7, longitude=37.6
    )
    db_session.add(location)
    db_session.flush()
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:old",
            event_date=date.today() - timedelta(days=200),
            event_number=1,
            summary_hash="old",
        )
    )
    db_session.commit()
    mark_inactive_locations_paused(db_session, location_ids=[location.id])
    assert location.is_paused is True

    changed, pause_changed, _ = apply_location_registry_flags(location, is_paused=False, is_cancelled=False)
    assert location.is_paused is True
    assert pause_changed is False

    # А пауза самого реестра правилом не снимается, даже при свежих стартах.
    apply_location_registry_flags(location, is_paused=True, is_cancelled=False)
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=f"{slug}:fresh",
            event_date=date.today() - timedelta(days=3),
            event_number=2,
            summary_hash="fresh",
        )
    )
    db_session.flush()
    mark_inactive_locations_paused(db_session, location_ids=[location.id])
    assert location.is_paused is True

    # Реестр снял свою паузу — площадка со свежими стартами снова в строю.
    apply_location_registry_flags(location, is_paused=False, is_cancelled=False)
    mark_inactive_locations_paused(db_session, location_ids=[location.id])
    assert location.is_paused is False


def test_manual_pause_drops_after_a_new_start(db_session: Session, s95_platform: Platform) -> None:
    """Ручное «не действует» снимается само, если площадка снова провела старт (Дмитрий 16.10.2026)."""
    from datetime import UTC, datetime

    from app.models import Event

    slug = f"comeback-{uuid4().hex[:8]}"
    set_at = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Вернувшаяся",
        is_paused=True,
        status_override="inactive",
        status_override_at=set_at,
        latitude=55.7,
        longitude=37.6,
    )
    db_session.add(location)
    db_session.flush()

    def start(day: date, number: int) -> None:
        db_session.add(
            Event(
                platform_id=s95_platform.id,
                location_id=location.id,
                external_event_key=f"{slug}:{number}",
                event_date=day,
                event_number=number,
                title="Старт",
            )
        )
        db_session.add(
            EventSummary(
                platform_id=s95_platform.id,
                location_id=location.id,
                external_event_key=f"{slug}:{number}",
                event_date=day,
                event_number=number,
                summary_hash=str(number),
            )
        )
        db_session.flush()

    # Старт в тот же день, что поставлен статус, — не «после него».
    start(date(2026, 10, 15), 1)
    mark_inactive_locations_paused(db_session, location_ids=[location.id], as_of=date(2026, 10, 16))
    assert location.status_override == "inactive"
    assert location.is_paused is True

    start(date(2026, 10, 17), 2)
    result = mark_inactive_locations_paused(db_session, location_ids=[location.id], as_of=date(2026, 10, 17))
    assert location.status_override is None
    assert location.status_override_at is None
    assert location.is_paused is False
    assert result.overrides_dropped == 1
