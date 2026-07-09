from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult
from app.services.location_catalog_service import LocationCatalogIndex
from app.time_format import normalize_finish_time_display

# Порядок предпочтения платформы, если одна и та же пробежка попала в несколько
# протоколов (кросслинк) — берём одну запись.
_PLATFORM_ORDER = {"five_verst": 0, "s95": 1, "parkrun": 2, "runpark": 3}

# Окно вокруг сегодняшней даты для годовщин прошлых лет: пробежки субботние,
# поэтому точная дата N лет назад редко совпадает — берём ±3 дня.
_ANNIVERSARY_WINDOW_DAYS = 3


def _anniversary_offset(run_date: date, today: date) -> int | None:
    """Сдвиг в днях от годовщины сегодняшней даты в году пробежки (None если дата невалидна)."""
    try:
        anniversary = date(run_date.year, today.month, today.day)
    except ValueError:
        return None  # 29 февраля в невисокосный год — пропускаем
    return (run_date - anniversary).days


def _run_entry(
    catalog_index: LocationCatalogIndex,
    run: RunResult,
    event: Event,
    location: Location,
    platform_code: str,
    participant: Participant,
    *,
    today: date,
) -> dict[str, object]:
    return {
        "years_ago": today.year - event.event_date.year,
        "event_date": event.event_date,
        "location_name": catalog_index.display_name(location, platform_code),
        "location_city": location.city,
        "platform_code": platform_code,
        "finish_time_display": normalize_finish_time_display(
            run.finish_time_sec, run.finish_time_display
        ),
        "finish_time_sec": run.finish_time_sec,
        "position": run.position,
        "is_pr": bool(run.is_pr),
        "event_url": resolve_activity_url(
            platform_code=platform_code,
            event_date=event.event_date,
            event_number=event.event_number,
            event_source_url=event.source_url,
            location_external_key=location.external_key,
            profile_url=participant.profile_url,
        ),
    }


def get_on_this_day(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
    today: date | None = None,
) -> dict[str, object]:
    """Годовщины: пробежки в этот же календарный день прошлых лет (±N дней).

    Смотрим только в прошлое — «в этот день год/N лет назад ты был на такой-то
    пробежке». Сегодняшняя пробежка (и вообще текущий год) НЕ считается годовщиной.
    Возвращает одну карточку (kind = "anniversary" | None) плюс сколько ещё
    пробежек было в этот же день в другие прошлые годы (also_count).
    """
    today = today or date.today()

    query = (
        db.query(RunResult, Event, Location, Platform.code, Participant)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.event_date > date(1970, 1, 1),
        )
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))

    catalog_index = LocationCatalogIndex(db)

    # Дедупликация по (дата, физическая локация) — кросслинкованное событие в
    # двух системах считаем одной пробежкой, оставляя приоритетную платформу.
    # Берём только пробежки ПРОШЛЫХ лет в окне ±N дней вокруг сегодняшней даты.
    by_key: dict[tuple[date, str], dict[str, object]] = {}
    for run, event, location, platform_code, participant in query.all():
        event_date = event.event_date
        offset = _anniversary_offset(event_date, today)
        if offset is None or abs(offset) > _ANNIVERSARY_WINDOW_DAYS:
            continue
        years_ago = today.year - event_date.year
        if years_ago < 1:
            continue  # текущий год (в т.ч. сегодня) — это не годовщина
        identity = catalog_index.canonical_identity_key(location, platform_code)
        key = (event_date, identity)
        entry = _run_entry(
            catalog_index, run, event, location, platform_code, participant, today=today
        )
        entry["day_offset"] = abs(offset)
        existing = by_key.get(key)
        if existing is None or _PLATFORM_ORDER.get(platform_code, 9) < _PLATFORM_ORDER.get(
            str(existing["platform_code"]), 9
        ):
            by_key[key] = entry

    entries = list(by_key.values())
    if not entries:
        return {
            "kind": None,
            "run": None,
            "runs": [],
            "also_count": 0,
            "today_iso": today.isoformat(),
        }

    # Чем ДАВНЕЕ пробежка, тем ценнее воспоминание — самая старая годовщина
    # становится героем карточки; при равенстве лет ближе к самой дате выше.
    entries.sort(key=lambda e: (-int(e["years_ago"]), int(e["day_offset"])))  # type: ignore[call-overload]
    for entry in entries:
        entry.pop("day_offset", None)
    return {
        "kind": "anniversary",
        "run": entries[0],  # герой карточки — самая давняя годовщина
        "runs": entries,  # все годовщины дня (для модалки «все пробежки в этот день»)
        "also_count": len(entries) - 1,
        "today_iso": today.isoformat(),
    }
