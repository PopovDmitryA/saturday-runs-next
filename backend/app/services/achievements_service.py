"""Цели на год и челленджи с уровнями (бронза/серебро/золото).

Челленджи — перенос правил из Grafana-дашборда «Челленджи»
(data/grafana/dashboards/3e54a2d8….json) + новые. Три вида:
- coverage: закрыть коллекцию (секунды финиша :00–:59, буквы алфавита, дни года…),
  уровни — доли коллекции (25%/50%/100%);
- counter: накопить события-совпадения (палиндромы, дежавю…), уровни — пороги;
- value: вырастить показатель (p-индекс, уникальные локации…), уровни — пороги.

Каждый челлендж (кроме «Семь дней» — коллекция из 7 клеток, не режется на тиры)
даёт три уровня сложности — easy/medium/hard, в каждом свои бронза/серебро/золото
(CHALLENGE_TIERS). Пороги easy/medium откалиброваны по фактическому распределению
прогресса зарегистрированных пользователей (307–401 чел., август 2026): easy
закрывается за первые полгода-год активности, medium — цель регулярного бегуна,
hard в основном воспроизводит прежние, «ветеранские» пороги. Тиры внутри тира
монотонно растут (bronze medium > gold easy и т.д.) — это гарантирует, что
"лучший" тир/уровень при показе бейджа однозначно определяется как самый
сложный тир, где вообще есть хоть один уровень (см. _challenge()).

Цели — пресеты (без свободного текста), можно выбрать любое число из GOAL_PRESETS
на год, хранятся в user_goals. Прогресс и прогноз «успеешь/не успеешь» считаются
на лету.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    LocationRating,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    UserGoal,
    VolunteerResult,
)
from app.services.location_catalog_service import LocationCatalogIndex, russian_parkrun_location_ids
from app.services.location_map_service import MAP_HISTORIC_PLATFORM
from app.services.platform_titles import PLATFORM_TITLES
from app.services.user_location_stats import _canonical_region, _normalize_geo_value
from app.time_format import normalize_finish_time_display
from app.volunteering_occasions import count_volunteering_for_platform, is_inventory_day, volunteer_occasion_dates

LEVEL_ORDER = ("bronze", "silver", "gold")

# Один тир на «весь» челлендж — единственный ключ словаря должен называться
# "solo" (сигнал фронту не рисовать вкладки сложности). Иначе три тира —
# всегда именно "easy"/"medium"/"hard" в этом порядке (порядок словаря важен:
# на нём строится выбор дефолтной вкладки и "лучшего" достижения).
TIER_LABELS: dict[str, str | None] = {
    "easy": "Лёгкий",
    "medium": "Средний",
    "hard": "Сложный",
    "solo": None,
}

# Реестр порогов: code -> {tier_key: (bronze, silver, gold)}. Единственное
# место в кодовой базе, где меняются числа уровней — история (level_dates)
# считается на лету по сырым событиям, так что правка порогов не требует ни
# миграции, ни бэкфилла.
CHALLENGE_TIERS: dict[str, dict[str, tuple[int, int, int]]] = {
    "seconds": {"easy": (3, 7, 10), "medium": (15, 25, 35), "hard": (40, 50, 60)},
    "positions": {"easy": (3, 7, 10), "medium": (15, 25, 40), "hard": (50, 75, 100)},
    "alphabet": {"easy": (2, 4, 7), "medium": (9, 13, 17), "hard": (20, 24, 28)},
    "calendar_days": {"easy": (5, 10, 25), "medium": (50, 100, 150), "hard": (200, 280, 366)},
    "start_numbers": {"easy": (5, 15, 30), "medium": (50, 80, 120), "hard": (150, 175, 200)},
    "start_numbers_pro": {"easy": (5, 15, 30), "medium": (50, 80, 120), "hard": (150, 175, 200)},
    "weekdays": {"solo": (2, 4, 7)},
    "palindrome": {"easy": (1, 3, 5), "medium": (7, 10, 13), "hard": (15, 20, 25)},
    "deja_vu": {"easy": (1, 2, 4), "medium": (8, 15, 25), "hard": (40, 70, 110)},
    "number_match": {"easy": (1, 2, 3), "medium": (5, 8, 12), "hard": (20, 35, 50)},
    "jubilee": {"easy": (1, 2, 3), "medium": (5, 7, 10), "hard": (15, 25, 50)},
    "p_index": {"easy": (2, 3, 4), "medium": (5, 6, 8), "hard": (10, 12, 15)},
    "pilgrim": {"easy": (3, 5, 10), "medium": (15, 25, 40), "hard": (60, 100, 150)},
    "regions": {"easy": (2, 3, 5), "medium": (8, 12, 18), "hard": (25, 40, 60)},
    "streak": {"easy": (4, 8, 12), "medium": (20, 30, 45), "hard": (60, 85, 120)},
    "best_year": {"easy": (5, 10, 20), "medium": (26, 34, 42), "hard": (45, 50, 55)},
    "inspector": {"easy": (1, 5, 10), "medium": (20, 40, 70), "hard": (100, 150, 200)},
    "reviewer": {"easy": (1, 3, 7), "medium": (15, 25, 50), "hard": (75, 100, 150)},
}

# Диапазоны номеров для «Нумератора» и «Нумератора ПРО». Одни и те же границы
# нужны и карточке челленджа, и таблице планирования — держим в одном месте,
# чтобы сетка ячеек и таблица не разъехались при правке порогов.
START_NUMBER_RANGES: dict[str, tuple[int, int]] = {
    "start_numbers": (1, 200),
    "start_numbers_pro": (201, 400),
}
# Сколько недельных окон показываем в планировании: ближайшая неделя, W+1, W+2.
START_NUMBER_PLAN_WEEKS = 3

# Русский алфавит для челленджа «Алфавит» (Ё объединяем с Е, твёрдый/мягкий знак
# и Ы не бывают первыми буквами названий).
_RU_ALPHABET = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"

_EPOCH_GUARD = date(1970, 1, 1)

# Рецензия — комментарий не короче стольких символов (звёзд мало, нужен текст).
REVIEW_MIN_COMMENT_LEN = 50


@dataclass(frozen=True)
class RatingRow:
    """Оценка старта. rated_on — дата САМОЙ оценки, а не старта: счётчик растёт
    в момент, когда человек оценил, и уровни датируются по нему."""

    rated_on: date
    platform_code: str
    is_review: bool


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


def _collect_rating_rows(db: Session, user_id: UUID) -> list[RatingRow]:
    """Оценки пользователя: когда оценил, в какой системе, рецензия или звёзды."""
    query = (
        db.query(LocationRating.created_at, LocationRating.platform_code, LocationRating.comment)
        .filter(LocationRating.user_id == user_id)
    )
    rows = [
        RatingRow(
            rated_on=created_at.date(),
            platform_code=platform_code,
            is_review=len((comment or "").strip()) >= REVIEW_MIN_COMMENT_LEN,
        )
        for created_at, platform_code, comment in query.all()
    ]
    rows.sort(key=lambda row: row.rated_on)
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


def _tier_payload(
    tier_key: str,
    thresholds: tuple[int, int, int],
    current: int,
    *,
    level_dates_fn: Callable[[dict[str, int]], dict[str, str | None]],
    to_next_label_fn: Callable[[dict[str, int], str], str | None] | None,
) -> dict[str, object]:
    levels = {"bronze": thresholds[0], "silver": thresholds[1], "gold": thresholds[2]}
    level, next_level, to_next = _resolve_level(current, levels)
    gold = levels["gold"]
    to_next_label = to_next_label_fn(levels, next_level) if (to_next_label_fn and next_level) else None
    return {
        "tier": tier_key,
        "label": TIER_LABELS.get(tier_key),
        "levels": levels,
        "target": gold,
        "level": level,
        "next_level": next_level,
        "to_next_level": to_next,
        "to_next_label": to_next_label,
        "pct": round(min(current / gold, 1.0) * 100, 1) if gold else 0.0,
        "level_dates": level_dates_fn(levels),
    }


def _challenge(
    *,
    code: str,
    title: str,
    icon: str,
    description: str,
    category: str,
    current: int,
    level_dates_fn: Callable[[dict[str, int]], dict[str, str | None]],
    unit: str | None = None,
    detail: dict[str, object] | None = None,
    to_next_label_fn: Callable[[dict[str, int], str], str | None] | None = None,
    tier_thresholds: dict[str, tuple[int, int, int]] | None = None,
) -> dict[str, object]:
    # tier_thresholds — пороги, посчитанные на лету вместо реестра: сегодня так
    # делает только «Алфавит», у которого размер коллекции зависит от фильтра
    # систем (см. _alphabet_tiers).
    thresholds_by_tier = tier_thresholds or CHALLENGE_TIERS[code]
    tiers = [
        _tier_payload(
            tier_key, thresholds, current, level_dates_fn=level_dates_fn, to_next_label_fn=to_next_label_fn
        )
        for tier_key, thresholds in thresholds_by_tier.items()
    ]
    # "Лучшее" достижение — самый сложный тир, где взят хоть один уровень.
    # Пороги тиров заданы монотонно растущими (bronze medium > gold easy), так
    # что взятие любого уровня в более сложном тире гарантированно означает
    # золото во всех более лёгких — простой проход по порядку с перезаписью
    # даёт тот же результат, что явный поиск "с конца".
    best_tier: str | None = None
    best_level: str | None = None
    for tier in tiers:
        if tier["level"] is not None:
            best_tier, best_level = str(tier["tier"]), str(tier["level"])
    default_tier = next((str(t["tier"]) for t in tiers if t["level"] != "gold"), str(tiers[-1]["tier"]))
    return {
        "code": code,
        "title": title,
        "icon": icon,
        "description": description,
        "category": category,
        "current": current,
        "unit": unit,
        "detail": detail or {},
        "tiers": tiers,
        "best_tier": best_tier,
        "best_level": best_level,
        "default_tier": default_tier,
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
    sorted_dates = sorted(row.event_date for row in first_by_second.values())
    return _challenge(
        code="seconds",
        title="60 секунд",
        icon="⏱️",
        description="Финишируй с каждой секундой на часах — от :00 до :59.",
        category="collection",
        current=len(first_by_second),
        unit="секунд",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _weekdays_challenge(rows: list[RunRow]) -> dict[str, object]:
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    first_by_weekday: dict[int, RunRow] = {}
    for row in rows:
        first_by_weekday.setdefault(row.event_date.weekday(), row)
    cells = [_cell(labels[index], first_by_weekday.get(index)) for index in range(7)]
    sorted_dates = sorted(row.event_date for row in first_by_weekday.values())
    return _challenge(
        code="weekdays",
        title="Семь дней",
        icon="📅",
        description="Пробеги парковый старт в каждый день недели — не субботой единой.",
        category="collection",
        current=len(first_by_weekday),
        unit="дней недели",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _positions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_by_position: dict[int, RunRow] = {}
    for row in rows:
        if row.position is not None and row.position > 0:
            first_by_position.setdefault(row.position % 100, row)
    cells = [_cell(f"{position:02d}", first_by_position.get(position)) for position in range(100)]
    sorted_dates = sorted(row.event_date for row in first_by_position.values())
    return _challenge(
        code="positions",
        title="Бинго позиций",
        icon="🎯",
        description="Финишируй на местах со всеми окончаниями от 00 до 99. Клетку определяют две последние цифры места: 18-е, 118-е и 218-е место закрывают одну и ту же клетку «18».",
        category="collection",
        current=len(first_by_position),
        unit="позиций",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _alphabet_available_names(db: Session, platform_code: str | None) -> dict[str, set[str]]:
    """Буква -> названия локаций, которыми её можно закрыть.

    Каталог букв зависит от выбранной системы: в 5 вёрстах нет ни одной
    локации на «Ц», в S95 нет «Д» — показывать в фильтре по системе буквы,
    которые в ней физически не закрыть, значит врать о цели.
    """
    query = (
        db.query(Location.name)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(Location.name.isnot(None))
    )
    if platform_code is None:
        # Сквозной вид: parkrun не считается (см. _alphabet_challenge), значит
        # и буквы его локаций в каталог не попадают.
        query = query.filter(Platform.code != MAP_HISTORIC_PLATFORM)
    else:
        query = query.filter(Platform.code == platform_code)
    available_names: dict[str, set[str]] = {}
    for (name,) in query.all():
        letter = _first_letter(name or "")
        if letter is not None and letter in _RU_ALPHABET:
            available_names.setdefault(letter, set()).add((name or "").strip())
    return available_names


def _alphabet_challenge(
    rows: list[RunRow],
    available_names: dict[str, set[str]],
    *,
    platform_code: str | None = None,
) -> dict[str, object]:
    """Parkrun-локации не считаются: у parkrun имена латиницей/местные, они
    искажали бы букву старта — challenge остаётся про русский алфавит.
    Исключение — фильтр по самому parkrun: там весь скоуп и есть parkrun, и
    буквы берутся с его же русскоязычных локаций.
    """
    skip_parkrun = platform_code is None
    first_by_letter: dict[str, RunRow] = {}
    for row in rows:
        if skip_parkrun and row.platform_code == MAP_HISTORIC_PLATFORM:
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
    sorted_dates = sorted(row.event_date for row in first_by_letter.values())
    return _challenge(
        code="alphabet",
        title="Алфавит",
        icon="🔤",
        description=_alphabet_description(len(letters), platform_code),
        category="collection",
        current=len(first_by_letter),
        unit="букв",
        detail={"letters": letters, "available": len(letters)},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
        tier_thresholds=_alphabet_tiers(len(letters)),
    )


def _alphabet_tiers(available: int) -> dict[str, tuple[int, int, int]]:
    """Пороги «Алфавита» под размер каталога букв.

    Пороги в CHALLENGE_TIERS откалиброваны на полный каталог (28 букв). Под
    фильтром систем букв меньше — у 5 вёрст их 26, у S95 17 — и прежняя шкала
    обещала недостижимое: золото за 28 букв там нельзя взять физически.
    Поэтому шкала линейно сжимается, а золото сложного тира приравнивается к
    числу доступных букв: собрал весь алфавит своей системы — взял золото.

    Порядок порогов остаётся строго возрастающим сквозь все тиры — на этом
    держится выбор «лучшего» тира в _challenge(). Если букв меньше, чем самих
    порогов, три тира в такой каталог не укладываются, и остаётся один тир
    ("solo", как у «Семи дней» — фронт тогда не рисует вкладки сложности).
    """
    reference = CHALLENGE_TIERS["alphabet"]
    full = reference["hard"][2]
    if available >= full:
        return reference

    flat = [value for thresholds in reference.values() for value in thresholds]
    if available >= len(flat):
        scaled: list[int] = []
        previous = 0
        for value in flat:
            scaled.append(max(1, round(value * available / full), previous + 1))
            previous = scaled[-1]
        # Золото сложного тира — ровно весь доступный алфавит, без округлений.
        scaled[-1] = available
        return {
            tier_key: (scaled[index * 3], scaled[index * 3 + 1], scaled[index * 3 + 2])
            for index, tier_key in enumerate(reference)
        }

    bronze = max(1, -(-available // 3))
    silver = max(bronze, -(-available * 2 // 3))
    return {"solo": (bronze, silver, max(silver, available))}


def _alphabet_description(available: int, platform_code: str | None) -> str:
    """Описание называет размер каталога и цену золота: под фильтром систем
    и букв меньше, и пороги другие (см. _alphabet_tiers)."""
    if platform_code is None:
        return (
            "Финишируй в локациях на каждую букву алфавита (считаются буквы, на которые есть "
            f"хотя бы одна локация — сейчас их {available}; parkrun в этом челлендже не учитывается). "
            f"Золото сложного уровня — все {available}."
        )
    title = PLATFORM_TITLES.get(platform_code, platform_code)
    if available == 0:
        return (
            f"Финишируй в локациях на каждую букву алфавита. У системы «{title}» нет локаций "
            "с русскими названиями — закрывать нечего, снимите фильтр систем."
        )
    letters = f"{available} {_plural_ru(available, ('букву', 'буквы', 'букв'))}"
    return (
        f"Финишируй в локациях на каждую букву алфавита. Выбрана система «{title}» — считаются "
        f"только её локации, а они дают {letters}. Пороги уровней подстроены под этот каталог: "
        f"золото даётся за все {available}."
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
    # hard-золото = 366 (весь календарь, включая 29 февраля) — больше дат физически не бывает.
    sorted_dates = sorted(row.event_date for row in first_by_day.values())
    return _challenge(
        code="calendar_days",
        title="Круглый год",
        icon="🗓️",
        description="Закрой каждую дату календаря — все 366 дней в году (год не важен).",
        category="collection",
        current=len(first_by_day),
        unit="дат",
        detail={"days": days},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


@dataclass(frozen=True)
class PredictedStart:
    """Один предсказанный старт: локация, дата, порядковый номер в своей системе."""

    platform_code: str
    number: int
    event_date: date
    location_name: str
    location_slug: str
    # Индекс недельного окна от сегодня: 0 — ближайшая неделя, 1 — W+1 и т.д.
    week_index: int


def _predict_upcoming_starts(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 3,
    max_number: int = 400,
) -> list[PredictedStart]:
    """Прогноз ближайших стартов: последний известный старт каждой активной
    локации + 1..weeks недель вперёд. Локации, у которых последний старт давно
    (прогноз уже в прошлом), отпадают сами. Номер старта считается ВНУТРИ своей
    системы (five_verst/s95/parkrun/runpark) — каждая платформа нумерует события
    независимо.

    Отсекаем только зарубежный parkrun: из 2859 активных локаций 2437 — его
    мировой каталог, и подсказка «Скоро: Westpark ≈ 01.08» предлагала номер,
    который человек закрыть не может (у нас от такой площадки лежат лишь строки
    наших же участников из их профилей — см. russian_parkrun_location_ids).

    Действующие системы берём целиком, независимо от страны: у 5 вёрст, s95 и
    RunPark есть площадки в Беларуси, Сербии, Грузии и далее, и для тамошних
    участников это домашние старты. Раньше фильтр шёл по стране, и вместе с
    мировым parkrun выкидывал Гомель с Нови-Садом.

    Окно недели считаем от сегодня (`week_index`), а не по счётчику цикла: у
    локаций разные даты последнего старта, и «+1 неделя» для отставшей локации
    попадает в то же календарное окно, что «+2 недели» для идущей вровень.
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
    russian_parkrun = russian_parkrun_location_ids(db, catalog_index)
    predictions: list[PredictedStart] = []
    for event_number, event_date, location, platform_code in query.all():
        if platform_code == MAP_HISTORIC_PLATFORM and location.id not in russian_parkrun:
            continue
        display_name = catalog_index.display_name(location, platform_code)
        for week in range(1, weeks + 1):
            predicted_number = event_number + week
            predicted_date = event_date + timedelta(weeks=week)
            if predicted_date < today:
                continue
            week_index = (predicted_date - today).days // 7
            if week_index >= weeks:
                continue
            if predicted_number > max_number:
                break
            predictions.append(
                PredictedStart(
                    platform_code=platform_code,
                    number=predicted_number,
                    event_date=predicted_date,
                    location_name=display_name,
                    location_slug=location.external_key.strip().lower(),
                    week_index=week_index,
                )
            )
    return predictions


def _upcoming_event_numbers(
    db: Session,
    *,
    today: date | None = None,
    weeks: int = 3,
    max_number: int = 400,
) -> dict[tuple[str, int], list[tuple[date, str]]]:
    """Прогноз для подсказок в ячейках: (платформа, номер) → отсортированные
    пары (дата, локация)."""
    upcoming: dict[tuple[str, int], list[tuple[date, str]]] = {}
    for item in _predict_upcoming_starts(db, today=today, weeks=weeks, max_number=max_number):
        upcoming.setdefault((item.platform_code, item.number), []).append(
            (item.event_date, item.location_name)
        )
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
        unit="номеров",
        detail={"cells": cells},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
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
    sorted_dates = sorted(date.fromisoformat(str(item["date"])) for item in items)
    return _challenge(
        code="palindrome",
        title="Палиндром",
        icon="🪞",
        description="Финишируй со временем-зеркалом: минуты читаются как секунды наоборот — 23:32, 21:12, 30:03.",
        category="coincidence",
        current=len(items),
        unit="палиндромов",
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
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
        unit="совпадений",
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
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
    sorted_dates = [date.fromisoformat(str(item["date"])) for item in items]
    return _challenge(
        code="number_match",
        title="Совпадение номеров",
        icon="🔗",
        description="Порядковый номер старта совпал с номером твоей пробежки: твоя 30-я — и старт №30.",
        category="coincidence",
        current=len(items),
        unit="совпадений",
        detail=detail,
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
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
    sorted_dates = [date.fromisoformat(str(item["date"])) for item in items]
    return _challenge(
        code="jubilee",
        title="Юбилейщик",
        icon="🎂",
        description="Участвуй в юбилейных стартах локаций — событиях с круглыми номерами №50, №100, №150…",
        category="coincidence",
        current=len(items),
        unit="юбилеев",
        detail={"items": items},
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
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
    counts_values = list(counts.values())

    def _to_next_label(levels: dict[str, int], next_level: str) -> str | None:
        needed = _runs_needed_for_p(counts_values, levels[next_level])
        return f"ещё {needed} {_plural_ru(needed, ('пробежка', 'пробежки', 'пробежек'))}"

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
        unit="",
        detail={"items": top},
        to_next_label_fn=_to_next_label,
        level_dates_fn=lambda levels: _p_index_level_dates(rows, levels),
    )


def _pilgrim_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_visit: dict[str, date] = {}
    for row in rows:
        if row.location_key not in first_visit or row.event_date < first_visit[row.location_key]:
            first_visit[row.location_key] = row.event_date
    sorted_dates = sorted(first_visit.values())
    return _challenge(
        code="pilgrim",
        title="Коллекционер локаций",
        icon="🗺️",
        description="Финишируй в как можно большем числе разных локаций.",
        category="scale",
        current=len(first_visit),
        unit="локаций",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _inspector_challenge(rating_rows: list[RatingRow]) -> dict[str, object]:
    sorted_dates = sorted(row.rated_on for row in rating_rows)
    return _challenge(
        code="inspector",
        title="Ревизор",
        icon="🔍",
        description="Оценивай старты, где бегал или волонтёрил: звёзды за организацию, трассу и атмосферу помогают другим выбрать, куда ехать.",
        category="community",
        current=len(sorted_dates),
        unit="оценок",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _reviewer_challenge(rating_rows: list[RatingRow]) -> dict[str, object]:
    sorted_dates = sorted(row.rated_on for row in rating_rows if row.is_review)
    return _challenge(
        code="reviewer",
        title="Рецензент",
        icon="📝",
        description=(
            f"Звёзд мало — расскажи словами. Отзыв от {REVIEW_MIN_COMMENT_LEN} символов: "
            "как встретили новичков, понятен ли брифинг, легко ли найти старт, что по трассе."
        ),
        category="community",
        current=len(sorted_dates),
        unit="рецензий",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
    )


def _regions_challenge(rows: list[RunRow]) -> dict[str, object]:
    first_visit: dict[str, date] = {}
    for row in rows:
        if not row.region:
            continue
        region = _canonical_region(row.region)
        if region not in first_visit or row.event_date < first_visit[region]:
            first_visit[region] = row.event_date
    sorted_dates = sorted(first_visit.values())
    return _challenge(
        code="regions",
        title="Путешественник",
        icon="🧭",
        description="Пробеги в разных регионах — от домашнего парка до другого конца страны.",
        category="scale",
        current=len(first_visit),
        unit="регионов",
        level_dates_fn=lambda levels: _level_dates(sorted_dates, levels),
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
    return _challenge(
        code="streak",
        title="Серийный бегун",
        icon="🔥",
        description="Лучшая серия суббот подряд — пробежкой или волонтёрством, без пропусков.",
        category="scale",
        current=streak,
        unit="суббот",
        level_dates_fn=lambda levels: _streak_level_dates(activity_dates, levels),
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
    return _challenge(
        code="best_year",
        title="Ударный год",
        icon="📈",
        description="Твой личный рекорд активности: сколько пробежек уместилось в один календарный год.",
        category="scale",
        current=best,
        unit="пробежек за год",
        level_dates_fn=lambda levels: _best_year_level_dates(rows, levels),
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
    rows: list[RunRow],
    vol_rows: dict[str, list[tuple[date, str]]],
    upcoming: dict[tuple[str, int], list[tuple[date, str]]],
    rating_rows: list[RatingRow],
    *,
    alphabet_names: dict[str, set[str]],
    platform_code: str | None,
) -> list[dict[str, object]]:
    return [
        _seconds_challenge(rows),
        _positions_challenge(rows),
        _alphabet_challenge(rows, alphabet_names, platform_code=platform_code),
        _calendar_days_challenge(rows),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers",
            title="Нумератор",
            description="Прими участие в стартах с порядковыми номерами от №1 до №200 — неважно, в какой системе получен каждый номер.",
            low=START_NUMBER_RANGES["start_numbers"][0],
            high=START_NUMBER_RANGES["start_numbers"][1],
        ),
        _start_numbers_range_challenge(
            rows,
            upcoming,
            code="start_numbers_pro",
            title="Нумератор ПРО",
            description="Для тех, кому мало двух сотен: старты с порядковыми номерами от №201 до №400 — неважно, в какой системе получен каждый номер.",
            low=START_NUMBER_RANGES["start_numbers_pro"][0],
            high=START_NUMBER_RANGES["start_numbers_pro"][1],
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
        _inspector_challenge(rating_rows),
        _reviewer_challenge(rating_rows),
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
    rating_rows: list[RatingRow],
    platform_code: str | None,
) -> tuple[
    list[RunRow],
    dict[str, list[tuple[date, str]]],
    dict[tuple[str, int], list[tuple[date, str]]],
    list[RatingRow],
]:
    """Сужает пробежки/волонтёрства/прогноз номеров/оценки до одной системы — для
    челленджей в разрезе платформы. None — без сужения (сквозной вид)."""
    if platform_code is None:
        return rows, vol_rows, upcoming, rating_rows
    scoped_rows = [row for row in rows if row.platform_code == platform_code]
    scoped_vol_rows = {code: v for code, v in vol_rows.items() if code == platform_code}
    scoped_upcoming = {key: v for key, v in upcoming.items() if key[0] == platform_code}
    scoped_rating_rows = [row for row in rating_rows if row.platform_code == platform_code]
    return scoped_rows, scoped_vol_rows, scoped_upcoming, scoped_rating_rows


class StartNumberPlanError(ValueError):
    """Запрошен челлендж, у которого нет диапазона номеров стартов."""


def build_start_numbers_plan(
    db: Session,
    user_id: UUID,
    *,
    code: str,
    platform_code: str | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Таблица планирования «Нумератора»: строка — номер старта, три колонки —
    ближайшая неделя, W+1, W+2, в ячейках локации, у которых старт с этим
    номером выпадает на эту неделю.

    Номер закрывается пробежкой В ЛЮБОЙ системе (см. _start_numbers_range_challenge),
    поэтому `done` считаем по номерам без привязки к платформе, а систему
    предсказанного старта показываем в ячейке — она подсказывает, куда ехать.

    platform_code повторяет фильтр систем со страницы достижений: когда там
    выбраны только 5 вёрст, и сам челлендж, и это планирование считаются по
    одной системе, иначе таблица предлагала бы старты, которые в текущем
    скоупе всё равно не засчитаются.
    """
    bounds = START_NUMBER_RANGES.get(code)
    if bounds is None:
        raise StartNumberPlanError(f"У челленджа «{code}» нет диапазона номеров стартов")
    low, high = bounds

    today = today or date.today()
    my_rows = _collect_run_rows(db, user_id)
    if platform_code:
        my_rows = [row for row in my_rows if row.platform_code == platform_code]
    done_numbers = {row.event_number for row in my_rows if row.event_number is not None}

    cells: dict[int, list[list[dict[str, object]]]] = {}
    for item in _predict_upcoming_starts(
        db, today=today, weeks=START_NUMBER_PLAN_WEEKS, max_number=high
    ):
        if not low <= item.number <= high:
            continue
        if platform_code and item.platform_code != platform_code:
            continue
        row_cells = cells.setdefault(item.number, [[] for _ in range(START_NUMBER_PLAN_WEEKS)])
        row_cells[item.week_index].append(
            {
                "location": item.location_name,
                "location_slug": item.location_slug,
                "platform_code": item.platform_code,
                "date": item.event_date.isoformat(),
            }
        )
    for row_cells in cells.values():
        for week_cell in row_cells:
            week_cell.sort(key=lambda entry: (str(entry["date"]), str(entry["location"])))

    rows = [
        {
            "number": number,
            "done": number in done_numbers,
            "weeks": cells.get(number) or [[] for _ in range(START_NUMBER_PLAN_WEEKS)],
        }
        for number in range(low, high + 1)
    ]
    return {
        "code": code,
        "platform_code": platform_code,
        "low": low,
        "high": high,
        "generated_for": today.isoformat(),
        # Только количество колонок: подписи «E / E+1 / E+2» строит фронт, а
        # границы окна в интерфейсе не нужны — дата стоит у каждой записи.
        "week_count": START_NUMBER_PLAN_WEEKS,
        "rows": rows,
    }


def compute_challenges(db: Session, user_id: UUID, platform_code: str | None = None) -> dict[str, object]:
    """platform_code сужает челленджи/бейджи/summary до одной системы —
    клубы (сквозные по конструкции — overall + по каждой платформе сразу)
    этим фильтром не затрагиваются и всегда считаются по полным данным."""
    rows = _collect_run_rows(db, user_id)
    vol_rows = _collect_volunteer_rows(db, user_id)
    upcoming = _upcoming_event_numbers(db)
    rating_rows = _collect_rating_rows(db, user_id)

    scoped_rows, scoped_vol_rows, scoped_upcoming, scoped_rating_rows = _scope_by_platform(
        rows, vol_rows, upcoming, rating_rows, platform_code
    )

    # Каталог букв «Алфавита» зависит от того же фильтра систем — читаем его
    # один раз на оба прогона списка челленджей (второй считает recent_delta).
    alphabet_names = _alphabet_available_names(db, platform_code)

    challenges = _build_challenge_list(
        scoped_rows,
        scoped_vol_rows,
        scoped_upcoming,
        scoped_rating_rows,
        alphabet_names=alphabet_names,
        platform_code=platform_code,
    )

    rows_before = _rows_before_last_activity(scoped_rows)
    if rows_before is not None and len(rows_before) < len(scoped_rows):
        previous: dict[str, int] = {
            str(c["code"]): int(c["current"])  # type: ignore[call-overload]
            for c in _build_challenge_list(
                rows_before,
                scoped_vol_rows,
                scoped_upcoming,
                scoped_rating_rows,
                alphabet_names=alphabet_names,
                platform_code=platform_code,
            )
        }
        for challenge in challenges:
            code = str(challenge["code"])
            delta = int(challenge["current"]) - previous.get(code, int(challenge["current"]))  # type: ignore[call-overload]
            challenge["recent_delta"] = max(delta, 0)

    def _tier(challenge: dict[str, object], tier_key: object) -> dict[str, object]:
        tiers = challenge["tiers"]
        assert isinstance(tiers, list)
        return next(t for t in tiers if t["tier"] == tier_key)

    summary = Counter(challenge["best_level"] for challenge in challenges if challenge["best_level"])
    badges = [
        {
            "code": challenge["code"],
            "title": challenge["title"],
            "icon": challenge["icon"],
            "level": challenge["best_level"],
            "tier": challenge["best_tier"],
            "tier_label": _tier(challenge, challenge["best_tier"])["label"],
            "achieved_at": _tier(challenge, challenge["best_tier"])["level_dates"].get(  # type: ignore[attr-defined]
                str(challenge["best_level"])
            ),
        }
        for challenge in sorted(
            (c for c in challenges if c["best_level"]),
            key=lambda c: LEVEL_ORDER.index(str(c["best_level"])),
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
