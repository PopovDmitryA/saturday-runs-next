"""Цели на год и челленджи с уровнями (бронза/серебро/золото).

Челленджи — перенос правил из Grafana-дашборда «Челленджи»
(data/grafana/dashboards/3e54a2d8….json) + новые. Три вида:
- coverage: закрыть коллекцию (секунды финиша :00–:59, буквы алфавита, дни года…),
  уровни — доли коллекции (25%/50%/100%);
- counter: накопить события-совпадения (палиндромы, дежавю…), уровни — пороги;
- value: вырастить показатель (p-индекс, уникальные локации…), уровни — пороги.

Цели — пресеты (без свободного текста), можно выбрать любое число из GOAL_PRESETS
на год, хранятся в user_goals. Прогресс и прогноз «успеешь/не успеешь» считаются
на лету.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, UserGoal, VolunteerResult
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.user_location_stats import _canonical_region, _normalize_geo_value
from app.time_format import normalize_finish_time_display
from app.volunteering_occasions import count_volunteering_for_platform, is_inventory_day, volunteer_occasion_dates

LEVEL_ORDER = ("bronze", "silver", "gold")

# Русский алфавит для челленджа «Алфавит» (Ё объединяем с Е, твёрдый/мягкий знак
# и Ы не бывают первыми буквами названий).
_RU_ALPHABET = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"

_EPOCH_GUARD = date(1970, 1, 1)


@dataclass(frozen=True)
class RunRow:
    event_date: date
    finish_time_sec: int | None
    position: int | None
    event_number: int | None
    location_name: str
    location_key: str
    region: str | None
    platform_code: str
    is_pr: bool


# ---------------------------------------------------------------------------
# Сбор данных


def _collect_run_rows(db: Session, user_id: UUID) -> list[RunRow]:
    from app.services.personal_record_service import user_secondary_crosslinked_run_ids

    query = (
        db.query(RunResult, Event, Location, Platform.code)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
    )
    secondary_ids = user_secondary_crosslinked_run_ids(db, user_id)
    if secondary_ids:
        query = query.filter(RunResult.id.notin_(secondary_ids))

    catalog_index = LocationCatalogIndex(db)
    rows: list[RunRow] = []
    for run, event, location, platform_code in query.all():
        rows.append(
            RunRow(
                event_date=event.event_date,
                finish_time_sec=run.finish_time_sec,
                position=run.position,
                event_number=event.event_number,
                location_name=catalog_index.display_name(location, platform_code),
                location_key=catalog_index.canonical_identity_key(location, platform_code),
                region=_normalize_geo_value(location.region),
                platform_code=platform_code,
                is_pr=bool(run.is_pr),
            )
        )
    rows.sort(key=lambda row: (row.event_date, row.location_key))
    return rows


def _collect_volunteer_rows(db: Session, user_id: UUID) -> dict[str, list[tuple[date, str]]]:
    """Волонтёрские строки по платформам: (дата, location_key)."""
    query = (
        db.query(Event.event_date, Location.external_key, Platform.code)
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == VolunteerResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            Event.is_test_event.is_(False),
            Event.event_date > _EPOCH_GUARD,
        )
    )
    by_platform: dict[str, list[tuple[date, str]]] = {}
    for event_date, location_key, platform_code in query.all():
        by_platform.setdefault(platform_code, []).append((event_date, location_key or "unknown"))
    return by_platform


def _count_volunteering(vol_rows: dict[str, list[tuple[date, str]]]) -> int:
    return sum(count_volunteering_for_platform(code, rows) for code, rows in vol_rows.items())


def _parkrun_volunteer_total(db: Session, user_id: UUID) -> int:
    """parkrun не публикует даты волонтёрств — только суммарный счётчик
    (profile_extra/роли), поэтому он не попадает в _collect_volunteer_rows
    (там события лежат на epoch-дате и отсекаются _EPOCH_GUARD). Здесь берём
    готовый общий счётчик, которым уже пользуется дашборд."""
    from app.parkrun.volunteer_credits import count_parkrun_volunteering

    link = (
        db.query(PlatformLink)
        .join(Platform, Platform.id == PlatformLink.platform_id)
        .filter(PlatformLink.user_id == user_id, Platform.code == "parkrun")
        .first()
    )
    if link is None or link.participant_id is None:
        return 0
    participant = db.query(Participant).filter(Participant.id == link.participant_id).one_or_none()
    if participant is None:
        return 0
    return count_parkrun_volunteering(db, participant, link.platform_id)


# ---------------------------------------------------------------------------
# Челленджи


def _plural_ru(count: int, forms: tuple[str, str, str]) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return forms[2]
    last = count % 10
    if last == 1:
        return forms[0]
    if last in (2, 3, 4):
        return forms[1]
    return forms[2]


def _resolve_level(current: int, levels: dict[str, int]) -> tuple[str | None, str | None, int | None]:
    """Достигнутый уровень + следующий уровень и сколько до него осталось."""
    achieved: str | None = None
    for level in LEVEL_ORDER:
        if current >= levels[level]:
            achieved = level
    for level in LEVEL_ORDER:
        if current < levels[level]:
            return achieved, level, levels[level] - current
    return achieved, None, None


def _level_dates(sorted_dates: list[date], levels: dict[str, int]) -> dict[str, str | None]:
    """Дата достижения каждого уровня: sorted_dates — дата события, добавившего
    +1 к счётчику, по возрастанию. k-я по счёту дата — момент, когда счётчик
    впервые достиг k, что и есть дата уровня с порогом k."""
    result: dict[str, str | None] = {}
    for level in LEVEL_ORDER:
        threshold = levels[level]
        result[level] = sorted_dates[threshold - 1].isoformat() if 1 <= threshold <= len(sorted_dates) else None
    return result


def _threshold_dates(sorted_dates: list[date], thresholds: tuple[int, ...]) -> dict[str, str | None]:
    """Как _level_dates, но для произвольного списка порогов (клубы)."""
    return {
        str(threshold): sorted_dates[threshold - 1].isoformat() if threshold <= len(sorted_dates) else None
        for threshold in thresholds
    }


def _challenge(
    *,
    code: str,
    title: str,
    icon: str,
    description: str,
    category: str,
    current: int,
    levels: dict[str, int],
    unit: str | None = None,
    detail: dict[str, object] | None = None,
    to_next_label: str | None = None,
    level_dates: dict[str, str | None] | None = None,
) -> dict[str, object]:
    level, next_level, to_next = _resolve_level(current, levels)
    gold = levels["gold"]
    return {
        "code": code,
        "title": title,
        "icon": icon,
        "description": description,
        "category": category,
        "current": current,
        "target": gold,
        "levels": levels,
        "level": level,
        "next_level": next_level,
        "to_next_level": to_next,
        "to_next_label": to_next_label,
        "pct": round(min(current / gold, 1.0) * 100, 1) if gold else 0.0,
        "unit": unit,
        "detail": detail or {},
        "level_dates": level_dates or {"bronze": None, "silver": None, "gold": None},
        # Насколько последняя пробежка продвинула счётчик — проставляется
        # снаружи (compute_challenges), по умолчанию 0.
        "recent_delta": 0,
    }


def _mmss_or_none(finish_time_sec: int | None) -> tuple[int, int] | None:
    """(минуты, секунды) для времён до часа — челленджи совпадений считаем по MM:SS."""
    if finish_time_sec is None or finish_time_sec <= 0 or finish_time_sec >= 3600:
        return None
    return finish_time_sec // 60, finish_time_sec % 60


def _time_display(finish_time_sec: int) -> str:
    """Короткий формат MM:SS для времён до часа (24:31, а не 00:24:31)."""
    if finish_time_sec >= 3600:
        return normalize_finish_time_display(finish_time_sec, None) or ""
    return f"{finish_time_sec // 60}:{finish_time_sec % 60:02d}"


def _first_letter(name: str) -> str | None:
    for char in name.strip().upper():
        if char.isalpha():
            return "Е" if char == "Ё" else char
        if char.isdigit():
            return None
    return None


def _cell(
    label: str,
    row: RunRow | None,
    *,
    hint: str | None = None,
    count: int | None = None,
) -> dict[str, object]:
    """Клетка коллекции: закрыта первой пробежкой row; hint — подсказка для незакрытых."""
    return {
        "label": label,
        "done": row is not None,
        "date": row.event_date.isoformat() if row else None,
        "location": row.location_name if row else None,
        "hint": hint,
        "platform_code": row.platform_code if row else None,
        "count": count,
    }


def _seconds_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_second: dict[int, RunRow] = {}
    count_by_second: dict[int, int] = {}
    for row in rows:
        mmss = _mmss_or_none(row.finish_time_sec)
        if mmss is None:
            continue
        second = mmss[1]
        first_by_second.setdefault(second, row)
        count_by_second[second] = count_by_second.get(second, 0) + 1
    cells = [
        _cell(f":{second:02d}", first_by_second.get(second), count=count_by_second.get(second))
        for second in range(60)
    ]
    levels = {"bronze": 40, "silver": 50, "gold": 60}
    sorted_dates = sorted(row.event_date for row in first_by_second.values())
    return _challenge(
        code="seconds",
        title="60 секунд",
        icon="⏱️",
        description="Финишируй с каждой секундой на часах — от :00 до :59.",
        category="collection",
        current=len(first_by_second),
        levels=levels,
        unit="секунд",
        detail={"cells": cells},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _weekdays_challenge(rows: list[RunRow]) -> dict[str, object]:
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    first_by_weekday: dict[int, RunRow] = {}
    for row in rows:
        first_by_weekday.setdefault(row.event_date.weekday(), row)
    cells = [_cell(labels[index], first_by_weekday.get(index)) for index in range(7)]
    levels = {"bronze": 2, "silver": 4, "gold": 7}
    sorted_dates = sorted(row.event_date for row in first_by_weekday.values())
    return _challenge(
        code="weekdays",
        title="Семь дней",
        icon="📅",
        description="Пробеги парковый старт в каждый день недели — не субботой единой.",
        category="collection",
        current=len(first_by_weekday),
        levels=levels,
        unit="дней недели",
        detail={"cells": cells},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _positions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_position: dict[int, RunRow] = {}
    for row in rows:
        if row.position is not None and row.position > 0:
            first_by_position.setdefault(row.position % 100, row)
    cells = [_cell(f"{position:02d}", first_by_position.get(position)) for position in range(100)]
    levels = {"bronze": 50, "silver": 75, "gold": 100}
    sorted_dates = sorted(row.event_date for row in first_by_position.values())
    return _challenge(
        code="positions",
        title="Бинго позиций",
        icon="🎯",
        description="Финишируй на местах со всеми окончаниями от 00 до 99. Клетку определяют две последние цифры места: 18-е, 118-е и 218-е место закрывают одну и ту же клетку «18».",
        category="collection",
        current=len(first_by_position),
        levels=levels,
        unit="позиций",
        detail={"cells": cells},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _alphabet_challenge(db: Session, rows: list[RunRow]) -> dict[str, object]:
    """Parkrun-локации не считаются: у parkrun имена латиницей/местные, они
    искажали бы букву старта — challenge остаётся про русский алфавит."""
    available_names: dict[str, set[str]] = {}
    for (name,) in db.query(Location.name).filter(Location.name.isnot(None)).all():
        letter = _first_letter(name or "")
        if letter is not None and letter in _RU_ALPHABET:
            available_names.setdefault(letter, set()).add((name or "").strip())
    first_by_letter: dict[str, RunRow] = {}
    for row in rows:
        if row.platform_code == "parkrun":
            continue
        letter = _first_letter(row.location_name)
        if letter is not None and letter in available_names:
            first_by_letter.setdefault(letter, row)
    letters: list[dict[str, object]] = []
    for letter in _RU_ALPHABET:
        if letter not in available_names:
            continue
        first_row = first_by_letter.get(letter)
        names = sorted(available_names[letter])
        letters.append(
            {
                "letter": letter,
                "done": first_row is not None,
                "date": first_row.event_date.isoformat() if first_row else None,
                "location": first_row.location_name if first_row else None,
                "locations": names[:8],
                "locations_more": max(len(names) - 8, 0),
                "platform_code": first_row.platform_code if first_row else None,
            }
        )
    levels = {"bronze": 10, "silver": 17, "gold": 28}
    sorted_dates = sorted(row.event_date for row in first_by_letter.values())
    return _challenge(
        code="alphabet",
        title="Алфавит",
        icon="🔤",
        description="Финишируй в локациях на каждую букву алфавита (считаются буквы, на которые есть хотя бы одна локация; parkrun в этом челлендже не учитывается).",
        category="collection",
        current=len(first_by_letter),
        levels=levels,
        unit="букв",
        detail={"letters": letters},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _calendar_days_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_day: dict[str, RunRow] = {}
    for row in rows:
        first_by_day.setdefault(f"{row.event_date.month:02d}-{row.event_date.day:02d}", row)
    days = [
        {
            "key": key,
            "date": row.event_date.isoformat(),
            "location": row.location_name,
            "platform_code": row.platform_code,
        }
        for key, row in sorted(first_by_day.items())
    ]
    # gold = 366 (весь календарь, включая 29 февраля) — больше дат физически не бывает.
    levels = {"bronze": 100, "silver": 200, "gold": 366}
    sorted_dates = sorted(row.event_date for row in first_by_day.values())
    return _challenge(
        code="calendar_days",
        title="Круглый год",
        icon="🗓️",
        description="Закрой каждую дату календаря — все 366 дней в году (год не важен).",
        category="collection",
        current=len(first_by_day),
        levels=levels,
        unit="дат",
        detail={"days": days},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _upcoming_event_numbers(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 3,
    max_number: int = 400,
) -> dict[tuple[str, int], list[tuple[date, str]]]:
    """Прогноз ближайших порядковых номеров стартов: последний известный старт
    каждой активной локации + 1..weeks недель вперёд. Локации, у которых
    последний старт давно (прогноз в прошлом), отпадают сами. Номер старта
    считается ВНУТРИ своей системы (five_verst/s95/parkrun/runpark) — каждая
    платформа нумерует свои события независимо, поэтому ключ — (платформа, номер).
    """
    today = today or date.today()
    latest = (
        db.query(
            Event.location_id.label("location_id"),
            func.max(Event.event_date).label("last_date"),
        )
        .filter(
            Event.is_test_event.is_(False),
            Event.event_number.isnot(None),
            Event.event_date > _EPOCH_GUARD,
        )
        .group_by(Event.location_id)
        .subquery()
    )
    query = (
        db.query(Event.event_number, Event.event_date, Location, Platform.code)
        .join(
            latest,
            (Event.location_id == latest.c.location_id) & (Event.event_date == latest.c.last_date),
        )
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            Event.event_number.isnot(None),
            Location.is_cancelled.is_(False),
            Location.is_paused.is_(False),
        )
    )
    catalog_index = LocationCatalogIndex(db)
    horizon = today + timedelta(weeks=weeks)
    upcoming: dict[tuple[str, int], list[tuple[date, str]]] = {}
    for event_number, event_date, location, platform_code in query.all():
        display_name = catalog_index.display_name(location, platform_code)
        for week in range(1, weeks + 1):
            predicted_number = event_number + week
            predicted_date = event_date + timedelta(weeks=week)
            if predicted_date < today or predicted_date > horizon:
                continue
            if predicted_number > max_number:
                break
            upcoming.setdefault((platform_code, predicted_number), []).append((predicted_date, display_name))
    for entries in upcoming.values():
        entries.sort()
    return upcoming


def _upcoming_hint(entries: list[tuple[date, str]] | None) -> str | None:
    if not entries:
        return None
    parts = [f"{name} ≈ {when.day:02d}.{when.month:02d}" for when, name in entries[:3]]
    more = len(entries) - 3
    suffix = f" и ещё {more}" if more > 0 else ""
    return "Скоро: " + ", ".join(parts) + suffix


def _start_numbers_range_challenge(
    rows: list[RunRow],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
    *,
    code: str,
    title: str,
    description: str,
    low: int,
    high: int,
    levels: dict[str, int],
) -> dict[str, object]:
    """Номер старта считается ВНУТРИ одной системы (каждая платформа нумерует
    события независимо — см. _upcoming_event_numbers), но само число в
    диапазоне засчитывается в общий счётчик, если получено В ЛЮБОЙ системе:
    старт №4 на s95 закрывает клетку "4" точно так же, как старт №4 на
    five_verst — платформы здесь не соревнуются друг с другом, просто у
    каждой цифры своя (первая по дате) система-источник, которая видна в
    подсказке ячейки."""
    first_by_number: dict[int, RunRow] = {}
    for row in rows:
        if row.event_number is not None and low <= row.event_number <= high:
            first_by_number.setdefault(row.event_number, row)
    upcoming_by_number: dict[int, list[tuple[date, str]]] = {}
    for (_platform_code, number), entries in upcoming.items():
        if low <= number <= high:
            upcoming_by_number.setdefault(number, []).extend(entries)
    for entries in upcoming_by_number.values():
        entries.sort()
    cells = [
        _cell(
            str(number),
            first_by_number.get(number),
            hint=_upcoming_hint(upcoming_by_number.get(number)),
        )
        for number in range(low, high + 1)
    ]
    sorted_dates = sorted(row.event_date for row in first_by_number.values())
    return _challenge(
        code=code,
        title=title,
        icon="🔢",
        description=description,
        category="collection",
        current=len(first_by_number),
        levels=levels,
        unit="номеров",
        detail={"cells": cells},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _palindrome_challenge(rows: list[RunRow]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in rows:
        mmss = _mmss_or_none(row.finish_time_sec)
        if mmss is None or row.finish_time_sec in seen:
            continue
        minutes, seconds = mmss
        if f"{minutes:02d}" == f"{seconds:02d}"[::-1]:
            seen.add(row.finish_time_sec)  # type: ignore[arg-type]
            items.append(
                {
                    "date": row.event_date.isoformat(),
                    "value": _time_display(row.finish_time_sec),  # type: ignore[arg-type]
                    "location": row.location_name,
                }
            )
    levels = {"bronze": 1, "silver": 10, "gold": 25}
    sorted_dates = sorted(date.fromisoformat(str(item["date"])) for item in items)
    return _challenge(
        code="palindrome",
        title="Палиндром",
        icon="🪞",
        description="Финишируй со временем-зеркалом: минуты читаются как секунды наоборот — 23:32, 21:12, 30:03.",
        category="coincidence",
        current=len(items),
        levels=levels,
        unit="палиндромов",
        detail={"items": items},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _deja_vu_challenge(rows: list[RunRow]) -> dict[str, object]:
    rows_by_time: dict[int, list[RunRow]] = {}
    for row in rows:
        if row.finish_time_sec is not None and row.finish_time_sec > 0:
            rows_by_time.setdefault(row.finish_time_sec, []).append(row)
    repeated = sorted(time_sec for time_sec, entries in rows_by_time.items() if len(entries) >= 2)
    items = [
        {
            "value": _time_display(time_sec),
            "count": len(rows_by_time[time_sec]),
            "occurrences": [
                {"date": entry.event_date.isoformat(), "location": entry.location_name}
                for entry in rows_by_time[time_sec]
            ],
        }
        for time_sec in repeated
    ]
    levels = {"bronze": 3, "silver": 15, "gold": 50}
    # Дежавю "случается" в момент ВТОРОГО финиша с этим временем — эта дата и
    # закрывает соответствующую клетку счётчика совпадений.
    sorted_dates = sorted(rows_by_time[time_sec][1].event_date for time_sec in repeated)
    return _challenge(
        code="deja_vu",
        title="Дежавю",
        icon="👯",
        description="Финишируй с одним и тем же временем — секунда в секунду — на разных пробежках.",
        category="coincidence",
        current=len(repeated),
        levels=levels,
        unit="совпадений",
        detail={"items": items},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _number_match_challenge(rows: list[RunRow]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        if row.event_number is not None and row.event_number == index:
            items.append(
                {
                    "date": row.event_date.isoformat(),
                    "value": f"№{row.event_number}",
                    "location": row.location_name,
                }
            )
    detail: dict[str, object] = {"items": items}
    if not items:
        # Пример, чтобы было видно, как записывается совпадение
        next_index = len(rows) + 1
        top_location = Counter(row.location_name for row in rows).most_common(1)
        detail["example"] = {
            "value": f"№{next_index}",
            "location": top_location[0][0] if top_location else "Кузьминки",
            "note": f"так запишется совпадение — твоя {next_index}-я пробежка на старте №{next_index}",
        }
    levels = {"bronze": 1, "silver": 10, "gold": 25}
    sorted_dates = [date.fromisoformat(str(item["date"])) for item in items]
    return _challenge(
        code="number_match",
        title="Совпадение номеров",
        icon="🔗",
        description="Порядковый номер старта совпал с номером твоей пробежки: твоя 30-я — и старт №30.",
        category="coincidence",
        current=len(items),
        levels=levels,
        unit="совпадений",
        detail=detail,
        level_dates=_level_dates(sorted_dates, levels),
    )


def _jubilee_challenge(rows: list[RunRow]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for row in rows:
        if row.event_number is not None and row.event_number > 0 and row.event_number % 50 == 0:
            items.append(
                {
                    "date": row.event_date.isoformat(),
                    "value": f"№{row.event_number}",
                    "location": row.location_name,
                }
            )
    levels = {"bronze": 1, "silver": 10, "gold": 25}
    sorted_dates = [date.fromisoformat(str(item["date"])) for item in items]
    return _challenge(
        code="jubilee",
        title="Юбилейщик",
        icon="🎂",
        description="Участвуй в юбилейных стартах локаций — событиях с круглыми номерами №50, №100, №150…",
        category="coincidence",
        current=len(items),
        levels=levels,
        unit="юбилеев",
        detail={"items": items},
        level_dates=_level_dates(sorted_dates, levels),
    )


def _runs_needed_for_p(counts: list[int], target_p: int) -> int:
    """Минимум добежек до p-индекса target_p: добиваем target_p самых
    «наполненных» локаций до target_p финишей (новые локации считаем с нуля)."""
    top = sorted(counts, reverse=True)[:target_p]
    top += [0] * (target_p - len(top))
    return sum(max(0, target_p - count) for count in top)


def _p_index_level_dates(rows: list[RunRow], levels: dict[str, int]) -> dict[str, str | None]:
    """Реплей истории в хронологическом порядке: после каждого финиша
    пересчитываем p-индекс и запоминаем первую дату, когда он достиг порога."""
    counts: Counter[str] = Counter()
    achieved: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    for row in rows:
        counts[row.location_key] += 1
        sorted_counts = sorted(counts.values(), reverse=True)
        p_index = 0
        for index, count in enumerate(sorted_counts, start=1):
            if count >= index:
                p_index = index
            else:
                break
        for level in LEVEL_ORDER:
            if achieved[level] is None and p_index >= levels[level]:
                achieved[level] = row.event_date
    return {level: (value.isoformat() if value else None) for level, value in achieved.items()}


def _p_index_challenge(rows: list[RunRow]) -> dict[str, object]:
    counts = Counter(row.location_key for row in rows)
    names: dict[str, str] = {}
    for row in rows:
        names.setdefault(row.location_key, row.location_name)
    sorted_counts = sorted(counts.values(), reverse=True)
    p_index = 0
    for index, count in enumerate(sorted_counts, start=1):
        if count >= index:
            p_index = index
        else:
            break
    levels = {"bronze": 3, "silver": 5, "gold": 10}
    _, next_level, _ = _resolve_level(p_index, levels)
    to_next_label: str | None = None
    if next_level is not None:
        needed = _runs_needed_for_p(list(counts.values()), levels[next_level])
        to_next_label = f"ещё {needed} {_plural_ru(needed, ('пробежка', 'пробежки', 'пробежек'))}"
    top = [
        {"location": names[key], "count": count}
        for key, count in counts.most_common(20)
    ]
    return _challenge(
        code="p_index",
        title="p-индекс",
        icon="🧮",
        description="p локаций, в каждой из которых минимум p финишей.",
        category="scale",
        current=p_index,
        levels=levels,
        unit="",
        detail={"items": top},
        to_next_label=to_next_label,
        level_dates=_p_index_level_dates(rows, levels),
    )


def _pilgrim_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_visit: dict[str, date] = {}
    for row in rows:
        if row.location_key not in first_visit or row.event_date < first_visit[row.location_key]:
            first_visit[row.location_key] = row.event_date
    levels = {"bronze": 25, "silver": 75, "gold": 125}
    sorted_dates = sorted(first_visit.values())
    return _challenge(
        code="pilgrim",
        title="Коллекционер локаций",
        icon="🗺️",
        description="Финишируй в как можно большем числе разных локаций.",
        category="scale",
        current=len(first_visit),
        levels=levels,
        unit="локаций",
        level_dates=_level_dates(sorted_dates, levels),
    )


def _regions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_visit: dict[str, date] = {}
    for row in rows:
        if not row.region:
            continue
        region = _canonical_region(row.region)
        if region not in first_visit or row.event_date < first_visit[region]:
            first_visit[region] = row.event_date
    levels = {"bronze": 10, "silver": 20, "gold": 30}
    sorted_dates = sorted(first_visit.values())
    return _challenge(
        code="regions",
        title="Путешественник",
        icon="🧭",
        description="Пробеги в разных регионах — от домашнего парка до другого конца страны.",
        category="scale",
        current=len(first_visit),
        levels=levels,
        unit="регионов",
        level_dates=_level_dates(sorted_dates, levels),
    )


def _max_saturday_streak(dates: set[date]) -> int:
    saturdays = sorted(value for value in dates if value.weekday() == 5)
    best = 0
    current = 0
    previous: date | None = None
    for value in saturdays:
        current = current + 1 if previous is not None and value - previous == timedelta(days=7) else 1
        best = max(best, current)
        previous = value
    return best


def _streak_level_dates(activity_dates: set[date], levels: dict[str, int]) -> dict[str, str | None]:
    """Идём по субботам-с-активностью по порядку; в момент, когда текущая
    (не обязательно ещё финальная) серия впервые достигает порога — это и есть
    дата уровня, даже если серия потом прервётся."""
    saturdays = sorted(value for value in activity_dates if value.weekday() == 5)
    achieved: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    current_run = 0
    previous: date | None = None
    for value in saturdays:
        current_run = current_run + 1 if previous is not None and value - previous == timedelta(days=7) else 1
        previous = value
        for level in LEVEL_ORDER:
            if achieved[level] is None and current_run >= levels[level]:
                achieved[level] = value
    return {level: (value.isoformat() if value else None) for level, value in achieved.items()}


def _streak_challenge(rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]]) -> dict[str, object]:
    activity_dates = {row.event_date for row in rows}
    for platform_code, platform_rows in vol_rows.items():
        activity_dates |= volunteer_occasion_dates(platform_code, platform_rows)
    streak = _max_saturday_streak(activity_dates)
    levels = {"bronze": 10, "silver": 25, "gold": 50}
    return _challenge(
        code="streak",
        title="Серийный бегун",
        icon="🔥",
        description="Лучшая серия суббот подряд — пробежкой или волонтёрством, без пропусков.",
        category="scale",
        current=streak,
        levels=levels,
        unit="суббот",
        level_dates=_streak_level_dates(activity_dates, levels),
    )


def _best_year_level_dates(rows: list[RunRow], levels: dict[str, int]) -> dict[str, str | None]:
    """Для каждого года — дата, когда счётчик пробежек В ЭТОМ году впервые
    достиг порога; уровень "получен", как только хоть один год его достиг —
    берём самую раннюю такую дату среди всех лет."""
    dates_by_year: dict[int, list[date]] = {}
    for row in rows:
        dates_by_year.setdefault(row.event_date.year, []).append(row.event_date)
    best_date: dict[str, date | None] = {level: None for level in LEVEL_ORDER}
    for year_dates in dates_by_year.values():
        year_dates.sort()
        for level in LEVEL_ORDER:
            threshold = levels[level]
            if len(year_dates) >= threshold:
                candidate = year_dates[threshold - 1]
                current_best = best_date[level]
                if current_best is None or candidate < current_best:
                    best_date[level] = candidate
    return {level: (value.isoformat() if value else None) for level, value in best_date.items()}


def _best_year_challenge(rows: list[RunRow]) -> dict[str, object]:
    by_year = Counter(row.event_date.year for row in rows)
    best = max(by_year.values(), default=0)
    levels = {"bronze": 20, "silver": 30, "gold": 50}
    return _challenge(
        code="best_year",
        title="Ударный год",
        icon="📈",
        description="Твой личный рекорд активности: сколько пробежек уместилось в один календарный год.",
        category="scale",
        current=best,
        levels=levels,
        unit="пробежек за год",
        level_dates=_best_year_level_dates(rows, levels),
    )


# ---------------------------------------------------------------------------
# Клубы (10/25/50/100/250/500/1000)

CLUB_THRESHOLDS = (10, 25, 50, 100, 250, 500, 1000)


def _volunteer_occasion_instances(platform_code: str, rows: list[tuple[date, str]]) -> list[date]:
    """Даты волонтёрских occasion'ов (с повторами — на пятивёрстовской
    инвентаризации 1 января несколько локаций в один день считаются разными
    occasion'ами). Длина результата совпадает с count_volunteering_for_platform."""
    if platform_code == "five_verst":
        seen_inventory: set[tuple[date, str]] = set()
        seen_regular: set[date] = set()
        instances: list[date] = []
        for event_date, location_key in sorted(rows):
            if is_inventory_day(event_date):
                key = (event_date, location_key or "unknown")
                if key not in seen_inventory:
                    seen_inventory.add(key)
                    instances.append(event_date)
            elif event_date not in seen_regular:
                seen_regular.add(event_date)
                instances.append(event_date)
        return sorted(instances)
    seen: set[date] = set()
    instances = []
    for event_date, _location_key in sorted(rows):
        if event_date not in seen:
            seen.add(event_date)
            instances.append(event_date)
    return instances


def _club_entry(
    code: str, title: str, icon: str, dates: list[date], *, extra_count: int = 0
) -> dict[str, object]:
    """extra_count — волонтёрства без известной даты (parkrun: только общий
    счётчик), добавляются к current, но не могут дать level_dates для
    порогов за пределами len(dates)."""
    current = len(dates) + extra_count
    sorted_dates = sorted(dates)
    earned = [threshold for threshold in CLUB_THRESHOLDS if current >= threshold]
    next_threshold = next((threshold for threshold in CLUB_THRESHOLDS if current < threshold), None)
    previous = earned[-1] if earned else 0
    if next_threshold is None:
        pct = 100.0
    else:
        pct = round((current - previous) / (next_threshold - previous) * 100, 1)
    return {
        "code": code,
        "title": title,
        "icon": icon,
        "current": current,
        "thresholds": list(CLUB_THRESHOLDS),
        "earned": earned,
        "next_threshold": next_threshold,
        "to_next": next_threshold - current if next_threshold is not None else None,
        "pct_to_next": pct,
        "level_dates": _threshold_dates(sorted_dates, CLUB_THRESHOLDS),
    }


def _compute_clubs(
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    parkrun_volunteer_total: int = 0,
) -> dict[str, object]:
    all_vol_instances: list[date] = []
    for code, platform_rows in vol_rows.items():
        all_vol_instances.extend(_volunteer_occasion_instances(code, platform_rows))
    overall = [
        _club_entry("runs", "Пробежки", "🏃", [row.event_date for row in rows]),
        _club_entry(
            "volunteering", "Волонтёрства", "💚", all_vol_instances, extra_count=parkrun_volunteer_total
        ),
    ]
    runs_by_platform: dict[str, list[date]] = {}
    for row in rows:
        runs_by_platform.setdefault(row.platform_code, []).append(row.event_date)
    platform_codes = set(runs_by_platform) | set(vol_rows)
    if parkrun_volunteer_total > 0:
        platform_codes.add("parkrun")
    platforms = []
    for code in sorted(platform_codes, key=lambda code: -len(runs_by_platform.get(code, []))):
        run_dates = runs_by_platform.get(code, [])
        vol_dates = _volunteer_occasion_instances(code, vol_rows.get(code, []))
        vol_extra = parkrun_volunteer_total if code == "parkrun" else 0
        if not run_dates and not vol_dates and not vol_extra:
            continue
        platforms.append(
            {
                "platform_code": code,
                "entries": [
                    _club_entry("runs", "Пробежки", "🏃", run_dates),
                    _club_entry("volunteering", "Волонтёрства", "💚", vol_dates, extra_count=vol_extra),
                ],
            }
        )
    return {"overall": overall, "platforms": platforms}


def _build_challenge_list(
    db: Session,
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
) -> list[dict[str, object]]:
    return [
        _seconds_challenge(rows),
        _positions_challenge(rows),
        _alphabet_challenge(db, rows),
        _calendar_days_challenge(rows),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers",
            title="Нумератор",
            description="Прими участие в стартах с порядковыми номерами от №1 до №200 — неважно, в какой системе получен каждый номер.",
            low=1,
            high=200,
            levels={"bronze": 50, "silver": 100, "gold": 200},
        ),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers_pro",
            title="Нумератор ПРО",
            description="Для тех, кому мало двух сотен: старты с порядковыми номерами от №201 до №400 — неважно, в какой системе получен каждый номер.",
            low=201,
            high=400,
            levels={"bronze": 50, "silver": 100, "gold": 200},
        ),
        _weekdays_challenge(rows),
        _palindrome_challenge(rows),
        _deja_vu_challenge(rows),
        _number_match_challenge(rows),
        _jubilee_challenge(rows),
        _p_index_challenge(rows),
        _pilgrim_challenge(rows),
        _regions_challenge(rows),
        _streak_challenge(rows, vol_rows),
        _best_year_challenge(rows),
    ]


def _rows_before_last_activity(rows: list[RunRow]) -> list[RunRow] | None:
    """Пробежки без самого последнего дня активности — чтобы вычислить, что
    именно продвинулось благодаря последней пробежке. None, если пробежек нет."""
    if not rows:
        return None
    last_date = rows[-1].event_date  # rows уже отсортированы по event_date
    return [row for row in rows if row.event_date < last_date]


def _scope_by_platform(
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
    platform_code: str | None,
) -> tuple[list[RunRow], dict[str, list[tuple[date, str]]], dict[tuple[str, int], list[tuple[date, str]]]]:
    """Сужает пробежки/волонтёрства/прогноз номеров до одной системы — для
    челленджей в разрезе платформы. None — без сужения (сквозной вид)."""
    if platform_code is None:
        return rows, vol_rows, upcoming
    scoped_rows = [row for row in rows if row.platform_code == platform_code]
    scoped_vol_rows = {code: v for code, v in vol_rows.items() if code == platform_code}
    scoped_upcoming = {key: v for key, v in upcoming.items() if key[0] == platform_code}
    return scoped_rows, scoped_vol_rows, scoped_upcoming


def compute_challenges(db: Session, user_id: UUID, platform_code: str | None = None) -> dict[str, object]:
    """platform_code сужает челленджи/бейджи/summary до одной системы —
    клубы (сквозные по конструкции — overall + по каждой платформе сразу)
    этим фильтром не затрагиваются и всегда считаются по полным данным."""
    rows = _collect_run_rows(db, user_id)
    vol_rows = _collect_volunteer_rows(db, user_id)
    upcoming = _upcoming_event_numbers(db)

    scoped_rows, scoped_vol_rows, scoped_upcoming = _scope_by_platform(rows, vol_rows, upcoming, platform_code)

    challenges = _build_challenge_list(db, scoped_rows, scoped_vol_rows, scoped_upcoming)

    rows_before = _rows_before_last_activity(scoped_rows)
    if rows_before is not None and len(rows_before) < len(scoped_rows):
        previous: dict[str, int] = {
            str(c["code"]): int(c["current"])  # type: ignore[call-overload]
            for c in _build_challenge_list(db, rows_before, scoped_vol_rows, scoped_upcoming)
        }
        for challenge in challenges:
            code = str(challenge["code"])
            delta = int(challenge["current"]) - previous.get(code, int(challenge["current"]))  # type: ignore[call-overload]
            challenge["recent_delta"] = max(delta, 0)

    summary = Counter(challenge["level"] for challenge in challenges if challenge["level"])
    badges = [
        {
            "code": challenge["code"],
            "title": challenge["title"],
            "icon": challenge["icon"],
            "level": challenge["level"],
            "achieved_at": challenge["level_dates"].get(challenge["level"]),  # type: ignore[attr-defined]
        }
        for challenge in sorted(
            (c for c in challenges if c["level"]),
            key=lambda c: LEVEL_ORDER.index(str(c["level"])),
            reverse=True,
        )
    ]
    return {
        "challenges": challenges,
        "badges": badges,
        "summary": {
            "gold": summary.get("gold", 0),
            "silver": summary.get("silver", 0),
            "bronze": summary.get("bronze", 0),
            "total": len(challenges),
        },
        "clubs": _compute_clubs(rows, vol_rows, _parkrun_volunteer_total(db, user_id)),
    }


# ---------------------------------------------------------------------------
# Цели на год

@dataclass(frozen=True)
class GoalPreset:
    title: str
    icon: str
    unit: str
    # count | time | streak
    kind: str
    default_target: int
    min: int
    max: int
    description: str


GOAL_PRESETS: dict[str, GoalPreset] = {
    "runs_year": GoalPreset(
        title="Пробежки за год",
        icon="🏃",
        unit="пробежек",
        kind="count",
        default_target=50,
        min=1,
        max=200,
        description="Сколько парковых пробежек пробежать в этом году.",
    ),
    "volunteering_year": GoalPreset(
        title="Волонтёрства за год",
        icon="💚",
        unit="волонтёрств",
        kind="count",
        default_target=12,
        min=1,
        max=200,
        description="Сколько раз помочь на стартах в этом году.",
    ),
    "new_locations_year": GoalPreset(
        title="Новые локации",
        icon="🗺️",
        unit="локаций",
        kind="count",
        default_target=10,
        min=1,
        max=100,
        description="Открыть новые парки — локации, где ты ещё не финишировал.",
    ),
    "new_regions_year": GoalPreset(
        title="Новые регионы",
        icon="🧭",
        unit="регионов",
        kind="count",
        default_target=3,
        min=1,
        max=50,
        description="Пробежать в регионах, где ты ещё не бегал.",
    ),
    "finish_under": GoalPreset(
        title="Выбежать из времени",
        icon="⏱️",
        unit="",
        kind="time",
        default_target=25 * 60,
        min=12 * 60,
        max=90 * 60,
        description="Финишировать быстрее целевого времени хотя бы раз за год.",
    ),
    "saturday_streak": GoalPreset(
        title="Серия суббот",
        icon="🔥",
        unit="суббот подряд",
        kind="streak",
        default_target=10,
        min=2,
        max=52,
        description="Собрать серию суббот подряд — пробежкой или волонтёрством.",
    ),
    "saturday_consistency_year": GoalPreset(
        title="Регулярность",
        icon="📆",
        unit="%",
        kind="percent",
        default_target=50,
        min=10,
        max=100,
        description="Будь активен минимум в такую долю суббот года — пробежкой или волонтёрством, не обязательно подряд.",
    ),
    "pr_count_year": GoalPreset(
        title="Личные рекорды",
        icon="🏆",
        unit="рекордов",
        kind="count",
        default_target=3,
        min=1,
        max=20,
        description="Сколько раз обновить личный рекорд на платформе в этом году.",
    ),
}


def _year_fraction_elapsed(today: date) -> float:
    year_start = date(today.year, 1, 1)
    year_end = date(today.year, 12, 31)
    total_days = (year_end - year_start).days + 1
    elapsed = (today - year_start).days + 1
    return max(min(elapsed / total_days, 1.0), 1 / total_days)


def _saturdays_left(today: date) -> int:
    year_end = date(today.year, 12, 31)
    next_saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    if next_saturday > year_end:
        return 0
    return (year_end - next_saturday).days // 7 + 1


def _saturdays_of_year(year: int) -> list[date]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() == 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _year_activity_dates(year: int, rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]]) -> set[date]:
    year_dates = {row.event_date for row in rows if row.event_date.year == year}
    for code, platform_rows in vol_rows.items():
        year_dates |= {
            d for d in volunteer_occasion_dates(code, [(d, key) for d, key in platform_rows if d.year == year])
        }
    return year_dates


def _preset_current(
    goal_type: str,
    year: int,
    *,
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    today: date,
) -> tuple[int, str | None]:
    """Текущее значение метрики пресета за год — НЕ зависит от target_value,
    поэтому годится и для активных целей, и для превью ещё не выбранных
    пресетов в модалке настройки."""
    year_rows = [row for row in rows if row.event_date.year == year]

    if goal_type == "runs_year":
        return len(year_rows), None
    if goal_type == "volunteering_year":
        year_vol = {
            code: [(d, key) for d, key in platform_rows if d.year == year]
            for code, platform_rows in vol_rows.items()
        }
        return _count_volunteering(year_vol), None
    if goal_type == "new_locations_year":
        first_visits: dict[str, date] = {}
        for row in rows:
            if row.location_key not in first_visits or row.event_date < first_visits[row.location_key]:
                first_visits[row.location_key] = row.event_date
        return sum(1 for value in first_visits.values() if value.year == year), None
    if goal_type == "new_regions_year":
        first_regions: dict[str, date] = {}
        for row in rows:
            if not row.region:
                continue
            region = _canonical_region(row.region)
            if region not in first_regions or row.event_date < first_regions[region]:
                first_regions[region] = row.event_date
        return sum(1 for value in first_regions.values() if value.year == year), None
    if goal_type == "finish_under":
        times = [row.finish_time_sec for row in year_rows if row.finish_time_sec]
        best = min(times) if times else None
        return (best or 0), (_time_display(best) if best else None)
    if goal_type == "saturday_streak":
        year_dates = _year_activity_dates(year, rows, vol_rows)
        return _max_saturday_streak(year_dates), None
    if goal_type == "pr_count_year":
        return sum(1 for row in year_rows if row.is_pr), None
    if goal_type == "saturday_consistency_year":
        year_dates = _year_activity_dates(year, rows, vol_rows)
        all_saturdays = _saturdays_of_year(year)
        active_saturdays = sum(1 for day in all_saturdays if day in year_dates)
        # Текущий темп: доля АКТИВНЫХ суббот среди уже ПРОШЕДШИХ (не всего года) —
        # иначе в январе даже идеальная регулярность показывала бы единицы процентов.
        elapsed_saturdays = max(sum(1 for day in all_saturdays if day <= today), 1)
        return active_saturdays, f"{round(active_saturdays / elapsed_saturdays * 100)}%"
    return 0, None  # pragma: no cover — неизвестный пресет отфильтрован на записи


def _goal_progress(
    goal: UserGoal,
    *,
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    today: date,
    rows_before: list[RunRow] | None = None,
) -> dict[str, object]:
    preset = GOAL_PRESETS[goal.goal_type]
    year = goal.year
    current, current_display = _preset_current(goal.goal_type, year, rows=rows, vol_rows=vol_rows, today=today)
    on_track: bool | None = None
    forecast_value: int | None = None
    target_display: str | None = None

    # Насколько последняя пробежка продвинула именно эту цель — считается
    # одинаково для всех типов, кроме finish_under (там меньше = лучше).
    recent_delta = 0
    if rows_before is not None:
        current_before, _ = _preset_current(goal.goal_type, year, rows=rows_before, vol_rows=vol_rows, today=today)
        if goal.goal_type == "finish_under":
            recent_delta = max(current_before - current, 0) if current_before > 0 and current > 0 else 0
        else:
            recent_delta = max(current - current_before, 0)

    if goal.goal_type == "finish_under":
        best = current or None
        target_display = _time_display(goal.target_value)
        if best is None:
            pct = 0.0
        elif best <= goal.target_value:
            pct = 100.0
        else:
            pct = round(goal.target_value / best * 100, 1)
        done = best is not None and best <= goal.target_value
        return _goal_payload(
            goal, preset, current, pct, done, on_track, forecast_value, current_display, target_display, recent_delta
        )

    if goal.goal_type == "saturday_streak":
        year_dates = _year_activity_dates(year, rows, vol_rows)
        done = current >= goal.target_value
        # Достижима ли ещё цель: живая серия (заканчивающаяся последней субботой)
        # плюс оставшиеся субботы года.
        live_streak = 0
        if not done:
            last_saturday = today - timedelta(days=(today.weekday() - 5) % 7)
            expected = last_saturday
            if expected not in year_dates:
                expected -= timedelta(days=7)
            while expected in year_dates and expected.year == year:
                live_streak += 1
                expected -= timedelta(days=7)
            on_track = live_streak + _saturdays_left(today) >= goal.target_value
        pct = round(min(current / goal.target_value, 1.0) * 100, 1)
        return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, None, None, recent_delta)

    if goal.goal_type == "saturday_consistency_year":
        all_saturdays = _saturdays_of_year(year)
        total_saturdays = max(len(all_saturdays), 1)
        target_count = max(1, round(goal.target_value / 100 * total_saturdays))
        pct = round(min(current / target_count, 1.0) * 100, 1)
        done = current >= target_count
        if not done:
            remaining = sum(1 for day in all_saturdays if day > today)
            on_track = current + remaining >= target_count
        target_display = f"{goal.target_value}%"
        return _goal_payload(
            goal, preset, current, pct, done, on_track, forecast_value, current_display, target_display, recent_delta
        )

    # count-цели: прогресс + линейный прогноз к концу года
    pct = round(min(current / goal.target_value, 1.0) * 100, 1)
    done = current >= goal.target_value
    if not done and today.year == year:
        forecast_value = round(current / _year_fraction_elapsed(today))
        on_track = forecast_value >= goal.target_value
    return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, None, None, recent_delta)


def _goal_payload(
    goal: UserGoal,
    preset: GoalPreset,
    current: int,
    pct: float,
    done: bool,
    on_track: bool | None,
    forecast_value: int | None,
    current_display: str | None,
    target_display: str | None,
    recent_delta: int = 0,
) -> dict[str, object]:
    return {
        "goal_type": goal.goal_type,
        "year": goal.year,
        "target_value": goal.target_value,
        "title": preset.title,
        "icon": preset.icon,
        "unit": preset.unit,
        "kind": preset.kind,
        "current_value": current,
        "pct": pct,
        "done": done,
        "on_track": on_track,
        "forecast_value": forecast_value,
        "current_display": current_display,
        "target_display": target_display,
        # Насколько последняя пробежка продвинула цель (0, если не продвинула
        # или дельта не считалась — см. rows_before в _goal_progress).
        "recent_delta": recent_delta,
    }


def _presets_payload(
    year: int, *, rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]], today: date
) -> list[dict[str, object]]:
    result = []
    for code, preset in GOAL_PRESETS.items():
        current_value, current_display = _preset_current(code, year, rows=rows, vol_rows=vol_rows, today=today)
        result.append(
            {
                "goal_type": code,
                "title": preset.title,
                "icon": preset.icon,
                "unit": preset.unit,
                "kind": preset.kind,
                "default_target": preset.default_target,
                "min": preset.min,
                "max": preset.max,
                "description": preset.description,
                "current_value": current_value,
                "current_display": current_display,
            }
        )
    return result


def get_goals_payload(db: Session, user_id: UUID, *, today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    year = today.year
    goals = (
        db.query(UserGoal)
        .filter(UserGoal.user_id == user_id, UserGoal.year == year)
        .order_by(UserGoal.created_at)
        .all()
    )
    rows = _collect_run_rows(db, user_id)
    vol_rows = _collect_volunteer_rows(db, user_id)
    rows_before = _rows_before_last_activity(rows)
    return {
        "year": year,
        "max_goals": len(GOAL_PRESETS),
        "goals": [
            _goal_progress(goal, rows=rows, vol_rows=vol_rows, today=today, rows_before=rows_before)
            for goal in goals
            if goal.goal_type in GOAL_PRESETS
        ],
        "presets": _presets_payload(year, rows=rows, vol_rows=vol_rows, today=today),
    }


class GoalValidationError(ValueError):
    pass


def save_goals(
    db: Session,
    user_id: UUID,
    goals: list[tuple[str, int]],
    *,
    today: date | None = None,
) -> dict[str, object]:
    """Заменяет набор целей пользователя на текущий год."""
    today = today or date.today()
    year = today.year
    if len(goals) > len(GOAL_PRESETS):
        raise GoalValidationError("Нельзя выбрать больше целей, чем есть пресетов")
    seen: set[str] = set()
    for goal_type, target_value in goals:
        preset = GOAL_PRESETS.get(goal_type)
        if preset is None:
            raise GoalValidationError(f"Неизвестная цель: {goal_type}")
        if goal_type in seen:
            raise GoalValidationError(f"Цель повторяется: {goal_type}")
        seen.add(goal_type)
        if not (preset.min <= target_value <= preset.max):
            raise GoalValidationError(
                f"Значение цели «{preset.title}» должно быть от {preset.min} до {preset.max}"
            )

    existing = {
        goal.goal_type: goal
        for goal in db.query(UserGoal).filter(UserGoal.user_id == user_id, UserGoal.year == year).all()
    }
    requested = dict(goals)
    for goal_type, goal in existing.items():
        if goal_type not in requested:
            db.delete(goal)
        elif goal.target_value != requested[goal_type]:
            goal.target_value = requested[goal_type]
    for goal_type, target_value in requested.items():
        if goal_type not in existing:
            db.add(UserGoal(user_id=user_id, year=year, goal_type=goal_type, target_value=target_value))
    db.commit()
    return get_goals_payload(db, user_id, today=today)
