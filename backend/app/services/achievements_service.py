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

from app.models import Event, Location, Platform, PlatformLink, RunResult, UserGoal, VolunteerResult
from app.services.location_catalog_service import LocationCatalogIndex
from app.services.user_location_stats import _canonical_region, _normalize_geo_value
from app.time_format import normalize_finish_time_display
from app.volunteering_occasions import count_volunteering_for_platform, volunteer_occasion_dates

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
) -> dict[str, object]:
    """Клетка коллекции: закрыта первой пробежкой row; hint — подсказка для незакрытых."""
    return {
        "label": label,
        "done": row is not None,
        "date": row.event_date.isoformat() if row else None,
        "location": row.location_name if row else None,
        "hint": hint,
    }


def _seconds_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_second: dict[int, RunRow] = {}
    for row in rows:
        mmss = _mmss_or_none(row.finish_time_sec)
        if mmss is None:
            continue
        first_by_second.setdefault(mmss[1], row)
    cells = [_cell(f":{second:02d}", first_by_second.get(second)) for second in range(60)]
    return _challenge(
        code="seconds",
        title="60 секунд",
        icon="⏱️",
        description="Финишируй с каждой секундой на часах — от :00 до :59.",
        category="collection",
        current=len(first_by_second),
        levels={"bronze": 15, "silver": 30, "gold": 60},
        unit="секунд",
        detail={"cells": cells},
    )


def _weekdays_challenge(rows: list[RunRow]) -> dict[str, object]:
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    first_by_weekday: dict[int, RunRow] = {}
    for row in rows:
        first_by_weekday.setdefault(row.event_date.weekday(), row)
    cells = [_cell(labels[index], first_by_weekday.get(index)) for index in range(7)]
    return _challenge(
        code="weekdays",
        title="Семь дней",
        icon="📅",
        description="Пробеги парковый старт в каждый день недели — не субботой единой.",
        category="collection",
        current=len(first_by_weekday),
        levels={"bronze": 2, "silver": 4, "gold": 7},
        unit="дней недели",
        detail={"cells": cells},
    )


def _positions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_position: dict[int, RunRow] = {}
    for row in rows:
        if row.position is not None and row.position > 0:
            first_by_position.setdefault(row.position % 100, row)
    cells = [_cell(f"{position:02d}", first_by_position.get(position)) for position in range(100)]
    return _challenge(
        code="positions",
        title="Бинго позиций",
        icon="🎯",
        description="Финишируй на местах со всеми окончаниями от 00 до 99. Клетку определяют две последние цифры места: 18-е, 118-е и 218-е место закрывают одну и ту же клетку «18».",
        category="collection",
        current=len(first_by_position),
        levels={"bronze": 25, "silver": 50, "gold": 100},
        unit="позиций",
        detail={"cells": cells},
    )


def _alphabet_challenge(db: Session, rows: list[RunRow]) -> dict[str, object]:
    available_names: dict[str, set[str]] = {}
    for (name,) in db.query(Location.name).filter(Location.name.isnot(None)).all():
        letter = _first_letter(name or "")
        if letter is not None and letter in _RU_ALPHABET:
            available_names.setdefault(letter, set()).add((name or "").strip())
    first_by_letter: dict[str, RunRow] = {}
    for row in rows:
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
            }
        )
    total = max(len(available_names), 1)
    return _challenge(
        code="alphabet",
        title="Алфавит",
        icon="🔤",
        description="Финишируй в локациях на каждую букву алфавита (считаются буквы, на которые есть хотя бы одна локация).",
        category="collection",
        current=len(first_by_letter),
        levels={"bronze": max(total // 4, 1), "silver": max(total // 2, 1), "gold": total},
        unit="букв",
        detail={"letters": letters},
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
        }
        for key, row in sorted(first_by_day.items())
    ]
    return _challenge(
        code="calendar_days",
        title="Круглый год",
        icon="🗓️",
        description="Закрой каждую дату календаря — все 366 дней в году (год не важен).",
        category="collection",
        current=len(first_by_day),
        levels={"bronze": 30, "silver": 100, "gold": 366},
        unit="дат",
        detail={"days": days},
    )


def _upcoming_event_numbers(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 3,
    max_number: int = 400,
) -> dict[int, list[tuple[date, str]]]:
    """Прогноз ближайших порядковых номеров стартов: последний известный старт
    каждой активной локации + 1..weeks недель вперёд. Локации, у которых
    последний старт давно (прогноз в прошлом), отпадают сами."""
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
    upcoming: dict[int, list[tuple[date, str]]] = {}
    for event_number, event_date, location, platform_code in query.all():
        display_name = catalog_index.display_name(location, platform_code)
        for week in range(1, weeks + 1):
            predicted_number = event_number + week
            predicted_date = event_date + timedelta(weeks=week)
            if predicted_date < today or predicted_date > horizon:
                continue
            if predicted_number > max_number:
                break
            upcoming.setdefault(predicted_number, []).append((predicted_date, display_name))
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
    upcoming: dict[int, list[tuple[date, str]]],
    *,
    code: str,
    title: str,
    description: str,
    low: int,
    high: int,
) -> dict[str, object]:
    first_by_number: dict[int, RunRow] = {}
    for row in rows:
        if row.event_number is not None and low <= row.event_number <= high:
            first_by_number.setdefault(row.event_number, row)
    cells = [
        _cell(str(number), first_by_number.get(number), hint=_upcoming_hint(upcoming.get(number)))
        for number in range(low, high + 1)
    ]
    total = high - low + 1
    return _challenge(
        code=code,
        title=title,
        icon="🔢",
        description=description,
        category="collection",
        current=len(first_by_number),
        levels={"bronze": total // 8, "silver": max(total * 3 // 8, 1), "gold": total},
        unit="номеров",
        detail={"cells": cells},
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
    return _challenge(
        code="palindrome",
        title="Палиндром",
        icon="🪞",
        description="Финишируй со временем-зеркалом: минуты читаются как секунды наоборот — 23:32, 21:12, 30:03.",
        category="coincidence",
        current=len(items),
        levels={"bronze": 1, "silver": 3, "gold": 5},
        unit="палиндромов",
        detail={"items": items[:20]},
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
                for entry in rows_by_time[time_sec][:4]
            ],
        }
        for time_sec in repeated
    ]
    return _challenge(
        code="deja_vu",
        title="Дежавю",
        icon="👯",
        description="Финишируй с одним и тем же временем — секунда в секунду — на разных пробежках.",
        category="coincidence",
        current=len(repeated),
        levels={"bronze": 1, "silver": 5, "gold": 10},
        unit="совпадений",
        detail={"items": items[:20]},
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
    detail: dict[str, object] = {"items": items[:20]}
    if not items:
        # Пример, чтобы было видно, как записывается совпадение
        next_index = len(rows) + 1
        top_location = Counter(row.location_name for row in rows).most_common(1)
        detail["example"] = {
            "value": f"№{next_index}",
            "location": top_location[0][0] if top_location else "Кузьминки",
            "note": f"так запишется совпадение — твоя {next_index}-я пробежка на старте №{next_index}",
        }
    return _challenge(
        code="number_match",
        title="Совпадение номеров",
        icon="🔗",
        description="Порядковый номер старта совпал с номером твоей пробежки: твоя 30-я — и старт №30.",
        category="coincidence",
        current=len(items),
        levels={"bronze": 1, "silver": 3, "gold": 5},
        unit="совпадений",
        detail=detail,
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
    return _challenge(
        code="jubilee",
        title="Юбилейщик",
        icon="🎂",
        description="Участвуй в юбилейных стартах локаций — событиях с круглыми номерами №50, №100, №150…",
        category="coincidence",
        current=len(items),
        levels={"bronze": 1, "silver": 5, "gold": 15},
        unit="юбилеев",
        detail={"items": items[:20]},
    )


def _runs_needed_for_p(counts: list[int], target_p: int) -> int:
    """Минимум добежек до p-индекса target_p: добиваем target_p самых
    «наполненных» локаций до target_p финишей (новые локации считаем с нуля)."""
    top = sorted(counts, reverse=True)[:target_p]
    top += [0] * (target_p - len(top))
    return sum(max(0, target_p - count) for count in top)


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
        for key, count in counts.most_common(10)
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
    )


def _pilgrim_challenge(rows: list[RunRow]) -> dict[str, object]:
    unique = len({row.location_key for row in rows})
    return _challenge(
        code="pilgrim",
        title="Паломник",
        icon="🗺️",
        description="Финишируй в как можно большем числе разных локаций.",
        category="scale",
        current=unique,
        levels={"bronze": 10, "silver": 25, "gold": 50},
        unit="локаций",
    )


def _regions_challenge(rows: list[RunRow]) -> dict[str, object]:
    regions = {_canonical_region(row.region) for row in rows if row.region}
    return _challenge(
        code="regions",
        title="Путешественник",
        icon="🧭",
        description="Пробеги в разных регионах — от домашнего парка до другого конца страны.",
        category="scale",
        current=len(regions),
        levels={"bronze": 3, "silver": 5, "gold": 10},
        unit="регионов",
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


def _streak_challenge(rows: list[RunRow], vol_rows: dict[str, list[tuple[date, str]]]) -> dict[str, object]:
    activity_dates = {row.event_date for row in rows}
    for platform_code, platform_rows in vol_rows.items():
        activity_dates |= volunteer_occasion_dates(platform_code, platform_rows)
    streak = _max_saturday_streak(activity_dates)
    return _challenge(
        code="streak",
        title="Серийный бегун",
        icon="🔥",
        description="Лучшая серия суббот подряд — пробежкой или волонтёрством, без пропусков.",
        category="scale",
        current=streak,
        levels={"bronze": 5, "silver": 10, "gold": 25},
        unit="суббот",
    )


def _best_year_challenge(rows: list[RunRow]) -> dict[str, object]:
    by_year = Counter(row.event_date.year for row in rows)
    best = max(by_year.values(), default=0)
    return _challenge(
        code="best_year",
        title="Ударный год",
        icon="📈",
        description="Твой личный рекорд активности: сколько пробежек уместилось в один календарный год.",
        category="scale",
        current=best,
        levels={"bronze": 20, "silver": 30, "gold": 50},
        unit="пробежек за год",
    )


# ---------------------------------------------------------------------------
# Клубы (10/25/50/100/250/500/1000)

CLUB_THRESHOLDS = (10, 25, 50, 100, 250, 500, 1000)


def _club_entry(code: str, title: str, icon: str, current: int) -> dict[str, object]:
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
    }


def _compute_clubs(
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
) -> dict[str, object]:
    overall = [
        _club_entry("runs", "Пробежки", "🏃", len(rows)),
        _club_entry("volunteering", "Волонтёрства", "💚", _count_volunteering(vol_rows)),
    ]
    runs_by_platform = Counter(row.platform_code for row in rows)
    platform_codes = sorted(
        set(runs_by_platform) | set(vol_rows),
        key=lambda code: -runs_by_platform.get(code, 0),
    )
    platforms = []
    for code in platform_codes:
        runs_count = runs_by_platform.get(code, 0)
        vol_count = count_volunteering_for_platform(code, vol_rows.get(code, []))
        if runs_count == 0 and vol_count == 0:
            continue
        platforms.append(
            {
                "platform_code": code,
                "entries": [
                    _club_entry("runs", "Пробежки", "🏃", runs_count),
                    _club_entry("volunteering", "Волонтёрства", "💚", vol_count),
                ],
            }
        )
    return {"overall": overall, "platforms": platforms}


def compute_challenges(db: Session, user_id: UUID) -> dict[str, object]:
    rows = _collect_run_rows(db, user_id)
    vol_rows = _collect_volunteer_rows(db, user_id)
    upcoming = _upcoming_event_numbers(db)

    challenges = [
        _seconds_challenge(rows),
        _positions_challenge(rows),
        _alphabet_challenge(db, rows),
        _calendar_days_challenge(rows),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers",
            title="Нумератор",
            description="Прими участие в стартах с порядковыми номерами от №1 до №200.",
            low=1,
            high=200,
        ),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers_pro",
            title="Нумератор ПРО",
            description="Для тех, кому мало двух сотен: старты с порядковыми номерами от №201 до №400.",
            low=201,
            high=400,
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

    summary = Counter(challenge["level"] for challenge in challenges if challenge["level"])
    badges = [
        {
            "code": challenge["code"],
            "title": challenge["title"],
            "icon": challenge["icon"],
            "level": challenge["level"],
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
        "clubs": _compute_clubs(rows, vol_rows),
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


def _goal_progress(
    goal: UserGoal,
    *,
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    today: date,
) -> dict[str, object]:
    preset = GOAL_PRESETS[goal.goal_type]
    year = goal.year
    year_rows = [row for row in rows if row.event_date.year == year]
    current: int
    pct: float
    on_track: bool | None = None
    forecast_value: int | None = None
    current_display: str | None = None
    target_display: str | None = None

    if goal.goal_type == "runs_year":
        current = len(year_rows)
    elif goal.goal_type == "volunteering_year":
        year_vol = {
            code: [(d, key) for d, key in platform_rows if d.year == year]
            for code, platform_rows in vol_rows.items()
        }
        current = _count_volunteering(year_vol)
    elif goal.goal_type == "new_locations_year":
        first_visits: dict[str, date] = {}
        for row in rows:
            if row.location_key not in first_visits or row.event_date < first_visits[row.location_key]:
                first_visits[row.location_key] = row.event_date
        current = sum(1 for value in first_visits.values() if value.year == year)
    elif goal.goal_type == "new_regions_year":
        first_regions: dict[str, date] = {}
        for row in rows:
            if not row.region:
                continue
            region = _canonical_region(row.region)
            if region not in first_regions or row.event_date < first_regions[region]:
                first_regions[region] = row.event_date
        current = sum(1 for value in first_regions.values() if value.year == year)
    elif goal.goal_type == "finish_under":
        times = [row.finish_time_sec for row in year_rows if row.finish_time_sec]
        best = min(times) if times else None
        current = best or 0
        target_display = _time_display(goal.target_value)
        current_display = _time_display(best) if best else None
        if best is None:
            pct = 0.0
        elif best <= goal.target_value:
            pct = 100.0
        else:
            pct = round(goal.target_value / best * 100, 1)
        done = best is not None and best <= goal.target_value
        return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, current_display, target_display)
    elif goal.goal_type == "saturday_streak":
        year_dates = {row.event_date for row in year_rows}
        for code, platform_rows in vol_rows.items():
            year_dates |= {
                d for d in volunteer_occasion_dates(code, [(d, key) for d, key in platform_rows if d.year == year])
            }
        current = _max_saturday_streak(year_dates)
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
        return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, None, None)
    elif goal.goal_type == "pr_count_year":
        current = sum(1 for row in year_rows if row.is_pr)
    elif goal.goal_type == "saturday_consistency_year":
        year_dates = {row.event_date for row in year_rows}
        for code, platform_rows in vol_rows.items():
            year_dates |= {
                d for d in volunteer_occasion_dates(code, [(d, key) for d, key in platform_rows if d.year == year])
            }
        all_saturdays = _saturdays_of_year(year)
        active_saturdays = sum(1 for day in all_saturdays if day in year_dates)
        total_saturdays = max(len(all_saturdays), 1)
        target_count = max(1, round(goal.target_value / 100 * total_saturdays))
        current = active_saturdays
        pct = round(min(current / target_count, 1.0) * 100, 1)
        done = current >= target_count
        if not done:
            remaining = sum(1 for day in all_saturdays if day > today)
            on_track = current + remaining >= target_count
        # Текущий темп: доля АКТИВНЫХ суббот среди уже ПРОШЕДШИХ (не всего года) —
        # иначе в январе даже идеальная регулярность показывала бы единицы процентов.
        elapsed_saturdays = max(sum(1 for day in all_saturdays if day <= today), 1)
        current_display = f"{round(active_saturdays / elapsed_saturdays * 100)}%"
        target_display = f"{goal.target_value}%"
        return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, current_display, target_display)
    else:  # pragma: no cover — неизвестный пресет отфильтрован на записи
        current = 0

    # count-цели: прогресс + линейный прогноз к концу года
    pct = round(min(current / goal.target_value, 1.0) * 100, 1)
    done = current >= goal.target_value
    if not done and today.year == year:
        forecast_value = round(current / _year_fraction_elapsed(today))
        on_track = forecast_value >= goal.target_value
    return _goal_payload(goal, preset, current, pct, done, on_track, forecast_value, None, None)


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
    }


def _presets_payload() -> list[dict[str, object]]:
    return [
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
        }
        for code, preset in GOAL_PRESETS.items()
    ]


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
    return {
        "year": year,
        "max_goals": len(GOAL_PRESETS),
        "goals": [
            _goal_progress(goal, rows=rows, vol_rows=vol_rows, today=today)
            for goal in goals
            if goal.goal_type in GOAL_PRESETS
        ],
        "presets": _presets_payload(),
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
