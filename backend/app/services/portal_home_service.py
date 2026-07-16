"""Агрегаты публичной главной портала.

Один тяжёлый пересчёт по всей истории всех систем, результат целиком
кэшируется в Redis (паттерн как у locations:index). Кросслинк-дубли
событий (одна физическая пробежка в протоколах двух систем) исключаются
через LEFT JOIN на event_crosslinks; локации склеиваются в физические
через canonical identity каталога.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import redis
from sqlalchemy import String, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import (
    Event,
    EventCrosslink,
    EventSummary,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    VolunteerResult,
)
from app.schemas.portal import PortalHomeResponse
from app.services.location_catalog_service import LocationCatalogIndex

logger = logging.getLogger(__name__)

PORTAL_HOME_CACHE_KEY = "portal:home:v16"
PORTAL_HOME_CACHE_TTL_SECONDS = 6 * 60 * 60

DISTANCE_KM = 5
# Мировой рекорд на 5 км — 12:35; всё быстрее 13 минут в протоколах
# парковых пробежек — мусор парсинга, в рекорды не берём.
MIN_SANE_FINISH_SEC = 13 * 60
WEEK_RECORD_WINDOW_DAYS = 7
GEO_POINTS_LIMIT = 400
WEEK_RECORDS_LIMIT = 12
# У parkrun-волонтёрств и части легаси-записей даты обнулены в 1970 —
# такие события не относятся к реальной хронологии.
MIN_SANE_EVENT_DATE = date(2004, 1, 1)

PLATFORM_TITLES = {
    "five_verst": "5 вёрст",
    "s95": "S95",
    "parkrun": "parkrun",
    "runpark": "RunPark",
}
PLATFORM_ORDER = ["five_verst", "s95", "runpark", "parkrun"]
# На главной «активна» = система реально проводит старты сейчас;
# platforms.is_active в БД отражает работоспособность синка, не это.
# parkrun в России закрыт в 2022-м (зарубежные старты участников синкаются,
# но система для аудитории — архивная эпоха).
ACTIVE_PLATFORM_CODES = {"five_verst", "s95", "runpark"}


def _not_crosslink_secondary(query: Any) -> Any:
    """Исключить события-дубли (вторичная сторона кросслинка)."""
    return query.outerjoin(
        EventCrosslink, EventCrosslink.secondary_event_id == Event.id
    ).filter(EventCrosslink.id.is_(None))


def _gender_expr() -> Any:
    """Пол бегуна: 5 вёрст/RunPark из age_category, S95 из profile_extra."""
    return case(
        (
            Platform.code == "five_verst",
            case(
                (func.substr(Participant.age_category, 1, 1) == "М", "male"),
                (func.substr(Participant.age_category, 1, 1) == "Ж", "female"),
            ),
        ),
        (
            Platform.code.in_(["runpark", "parkrun"]),
            case(
                (func.substr(Participant.age_category, 2, 1) == "M", "male"),
                (func.substr(Participant.age_category, 2, 1) == "W", "female"),
            ),
        ),
        (
            Platform.code == "s95",
            Participant.profile_extra["platform_codes"]["gender"].astext,
        ),
    )


def clean_time_display(display: str | None, seconds: int | None) -> str:
    """Времена в БД бывают в виде «00:16:21» — приводим к «16:21»."""
    if seconds is not None:
        return format_finish_time(seconds)
    if not display:
        return ""
    return display.removeprefix("00:0").removeprefix("00:")


def format_finish_time(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class _EventRow:
    __slots__ = ("event_id", "location_id", "platform_code", "event_date", "finishers")

    def __init__(
        self,
        event_id: UUID,
        location_id: UUID,
        platform_code: str,
        event_date: date,
        finishers: int,
    ) -> None:
        self.event_id = event_id
        self.location_id = location_id
        self.platform_code = platform_code
        self.event_date = event_date
        self.finishers = finishers


def _load_events(db: Session) -> list[_EventRow]:
    """Все события без кросслинк-дублей, с честным числом финишёров.

    Финишёры: протокол → events.finishers_count → event_summaries
    (у parkrun-эры и части архивов протокола в БД нет).
    """
    protocol_counts: dict[UUID, int] = {
        event_id: int(count)
        for event_id, count in db.query(RunResult.event_id, func.count(RunResult.id))
        .group_by(RunResult.event_id)
        .all()
    }
    rows = (
        _not_crosslink_secondary(
            db.query(
                Event.id,
                Event.location_id,
                Platform.code,
                Event.event_date,
                Event.finishers_count,
                EventSummary.finishers_count,
            )
            .join(Platform, Platform.id == Event.platform_id)
            .outerjoin(EventSummary, EventSummary.event_id == Event.id)
        )
    ).filter(Event.event_date >= MIN_SANE_EVENT_DATE).all()
    events: list[_EventRow] = []
    for event_id, location_id, platform_code, event_date, event_cnt, summary_cnt in rows:
        finishers = protocol_counts.get(event_id) or event_cnt or summary_cnt or 0
        events.append(_EventRow(event_id, location_id, platform_code, event_date, finishers))
    return events


def _participants_total(db: Session) -> int:
    """Участники: профили систем; связанные на сайте профили одного
    человека (platform_links одного user) считаются одним участником."""
    runners = select(RunResult.participant_id).where(
        RunResult.participant_id.isnot(None)
    )
    total = int(
        db.query(func.count(func.distinct(RunResult.participant_id)))
        .filter(RunResult.participant_id.isnot(None))
        .scalar()
        or 0
    )
    linked_counts = (
        db.query(func.count(func.distinct(PlatformLink.participant_id)))
        .filter(
            PlatformLink.participant_id.isnot(None),
            PlatformLink.participant_id.in_(runners),
        )
        .group_by(PlatformLink.user_id)
        .having(func.count(func.distinct(PlatformLink.participant_id)) > 1)
        .all()
    )
    merged_extra = sum(int(count) - 1 for (count,) in linked_counts)
    return total - merged_extra


def _exclude_locations(query: Any, excluded_location_ids: list[UUID]) -> Any:
    if not excluded_location_ids:
        return query
    return query.filter(~Event.location_id.in_(excluded_location_ids))


def _participants_in_window(
    db: Session,
    window_start: date,
    window_end: date,
    excluded_location_ids: list[UUID],
) -> int:
    return int(
        _exclude_locations(
            _not_crosslink_secondary(
                db.query(func.count(func.distinct(RunResult.participant_id)))
                .select_from(RunResult)
                .join(Event, Event.id == RunResult.event_id)
            ).filter(
                RunResult.participant_id.isnot(None),
                Event.event_date >= window_start,
                Event.event_date <= window_end,
            ),
            excluded_location_ids,
        ).scalar()
        or 0
    )


def _pulse_flags(
    db: Session, pulse_date: date, excluded_location_ids: list[UUID]
) -> tuple[int, int]:
    row = _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                func.coalesce(
                    func.sum(case((RunResult.is_first_run.is_(True), 1), else_=0)), 0
                ),
                func.coalesce(func.sum(case((RunResult.is_pr.is_(True), 1), else_=0)), 0),
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
        ).filter(Event.event_date == pulse_date),
        excluded_location_ids,
    ).one()
    return int(row[0]), int(row[1])


def _pulse_volunteers(
    db: Session, pulse_date: date, excluded_location_ids: list[UUID]
) -> int:
    return int(
        _exclude_locations(
            _not_crosslink_secondary(
                db.query(func.count(VolunteerResult.id))
                .select_from(VolunteerResult)
                .join(Event, Event.id == VolunteerResult.event_id)
            ).filter(Event.event_date == pulse_date),
            excluded_location_ids,
        ).scalar()
        or 0
    )


def _course_minima_before(db: Session, week_start: date) -> list[Any]:
    """Минимум по локации и полу до начала недели + дата этого минимума.

    Дата достаётся лексикографическим минимумом строки
    «зеропаддед-секунды|дата» — одним проходом, без второго запроса.
    """
    gender = _gender_expr()
    packed = func.min(
        func.concat(
            func.lpad(cast(RunResult.finish_time_sec, String), 8, "0"),
            "|",
            cast(Event.event_date, String),
        )
    )
    return list(
        _not_crosslink_secondary(
            db.query(Event.location_id, gender, packed)
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Participant, Participant.id == RunResult.participant_id)
        )
        .filter(
            Event.event_date < week_start,
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec >= MIN_SANE_FINISH_SEC,
        )
        .group_by(Event.location_id, gender)
        .all()
    )


def _unpack_min(packed: str | None) -> tuple[int, date] | None:
    if not packed or "|" not in packed:
        return None
    sec_raw, date_raw = packed.split("|", 1)
    try:
        return int(sec_raw), date.fromisoformat(date_raw)
    except ValueError:
        return None


def _week_results(
    db: Session, week_start: date, week_end: date, excluded_location_ids: list[UUID]
) -> list[Any]:
    gender = _gender_expr()
    return list(
        _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                Event.id,
                Event.location_id,
                Platform.code,
                Event.event_date,
                gender,
                RunResult.finish_time_sec,
                RunResult.finish_time_display,
                Participant.display_name,
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Participant, Participant.id == RunResult.participant_id)
        )
        .filter(
            Event.event_date >= week_start,
            Event.event_date <= week_end,
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec >= MIN_SANE_FINISH_SEC,
        ),
        excluded_location_ids,
        ).all()
    )


def _fastest_by_platform(
    db: Session,
    excluded_location_ids: list[UUID],
    fresh_since: date | None = None,
) -> list[dict[str, Any]]:
    """Самый быстрый результат в каждой системе по полу: минимумы одним
    group by, затем один запрос за строками с этими временами.

    Дельта (`delta_sec`) заполняется ТОЛЬКО если рекорд обновлён не раньше
    `fresh_since` (последняя беговая неделя) — рекорды систем обновляются
    редко, показывать вечную дельту смысла нет (Дмитрий, 13.07.2026)."""
    gender = _gender_expr()
    minima = _exclude_locations(
        _not_crosslink_secondary(
            db.query(Platform.code, gender, func.min(RunResult.finish_time_sec))
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Participant, Participant.id == RunResult.participant_id)
        ).filter(
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec >= MIN_SANE_FINISH_SEC,
        ),
        excluded_location_ids,
    ).group_by(Platform.code, gender).all()
    wanted: dict[tuple[str, str], int] = {
        (code, g): int(sec)
        for code, g, sec in minima
        if g in ("male", "female") and sec is not None
    }
    if not wanted:
        return []
    rows = _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                Platform.code,
                gender,
                RunResult.finish_time_sec,
                RunResult.finish_time_display,
                Participant.display_name,
                Event.event_date,
                Event.location_id,
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Participant, Participant.id == RunResult.participant_id)
        ).filter(RunResult.finish_time_sec.in_(sorted(set(wanted.values())))),
        excluded_location_ids,
    ).all()
    picked: dict[tuple[str, str], dict[str, Any]] = {}
    for code, g, sec, display, runner, event_date, location_id in rows:
        key = (code, g or "")
        if wanted.get(key) != sec or key in picked:
            continue
        picked[key] = {
            "platform_code": code,
            "gender": g,
            "value_display": clean_time_display(display, sec),
            "runner_name": runner,
            "event_date": event_date,
            "_location_id": location_id,
        }
    ordered: list[dict[str, Any]] = []
    for code in PLATFORM_ORDER:
        for g in ("male", "female"):
            item = picked.get((code, g))
            if item is None:
                continue
            item["delta_sec"] = None
            is_fresh = fresh_since is not None and item["event_date"] >= fresh_since
            if is_fresh:
                previous_sec = _fastest_previous_sec(
                    db, excluded_location_ids, code, g, item["event_date"]
                )
                current_sec = wanted[(code, g)]
                if previous_sec is not None:
                    item["delta_sec"] = previous_sec - current_sec
            ordered.append(item)
    return ordered


def _fastest_previous_sec(
    db: Session,
    excluded_location_ids: list[UUID],
    platform_code: str,
    gender_value: str,
    before_date: date,
) -> int | None:
    """Рекорд, который держался до текущего лучшего результата (для дельты)."""
    gender = _gender_expr()
    result = _exclude_locations(
        _not_crosslink_secondary(
            db.query(func.min(RunResult.finish_time_sec))
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Participant, Participant.id == RunResult.participant_id)
        ).filter(
            Platform.code == platform_code,
            gender == gender_value,
            Event.event_date < before_date,
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec >= MIN_SANE_FINISH_SEC,
        ),
        excluded_location_ids,
    ).scalar()
    return int(result) if result is not None else None


def _avg_finish_by_platform(
    db: Session, excluded_location_ids: list[UUID]
) -> tuple[int | None, dict[str, int]]:
    """Среднее время финиша: общее и по системам (только вменяемые времена)."""
    rows = _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                Platform.code,
                func.count(RunResult.id),
                func.coalesce(func.sum(RunResult.finish_time_sec), 0),
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
        ).filter(
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec >= MIN_SANE_FINISH_SEC,
        ),
        excluded_location_ids,
    ).group_by(Platform.code).all()
    per_platform: dict[str, int] = {}
    total_count = 0
    total_sum = 0
    for code, count, sec_sum in rows:
        if count:
            per_platform[code] = int(sec_sum) // int(count)
            total_count += int(count)
            total_sum += int(sec_sum)
    overall = total_sum // total_count if total_count else None
    return overall, per_platform


def _gender_split(
    db: Session, excluded_location_ids: list[UUID]
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Финиши по полу: общий счётчик male/female и разбивка по системам.

    Учитываются только строки с определённым полом (у части parkrun/s95
    пол неизвестен — они просто не попадают в знаменатель)."""
    gender = _gender_expr()
    rows = _exclude_locations(
        _not_crosslink_secondary(
            db.query(Platform.code, gender, func.count(RunResult.id))
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
            .join(Participant, Participant.id == RunResult.participant_id)
        ),
        excluded_location_ids,
    ).group_by(Platform.code, gender).all()
    by_platform: dict[str, dict[str, int]] = {}
    total = {"male": 0, "female": 0}
    for code, gender_value, count in rows:
        if gender_value not in ("male", "female"):
            continue
        slot = by_platform.setdefault(code, {"male": 0, "female": 0})
        slot[gender_value] += int(count)
        total[gender_value] += int(count)
    return total, by_platform


def _newcomers_by_year(
    db: Session, excluded_location_ids: list[UUID]
) -> dict[tuple[int, str], int]:
    """(год, платформа) → число новичков (первый счётный забег на платформе).

    Агрегация в БД, не в Python: RunResult на порядки больше строк, чем
    events (тот список уже агрегирован по протоколу)."""
    year_expr = func.extract("year", Event.event_date)
    rows = _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                year_expr,
                Platform.code,
                func.coalesce(
                    func.sum(case((RunResult.is_first_run.is_(True), 1), else_=0)), 0
                ),
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
        ).filter(Event.event_date >= MIN_SANE_EVENT_DATE),
        excluded_location_ids,
    ).group_by(year_expr, Platform.code).all()
    return {(int(year), code): int(count) for year, code, count in rows}


def _newcomers_by_date(
    db: Session,
    excluded_location_ids: list[UUID],
    start: date,
    end: date,
) -> dict[tuple[date, str], int]:
    """(дата, платформа) → число новичков внутри окна дат — бакетится по
    неделям в Python той же формулой, что и остальные недельные графики."""
    rows = _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                Event.event_date,
                Platform.code,
                func.coalesce(
                    func.sum(case((RunResult.is_first_run.is_(True), 1), else_=0)), 0
                ),
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
            .join(Platform, Platform.id == Event.platform_id)
        ).filter(Event.event_date >= start, Event.event_date <= end),
        excluded_location_ids,
    ).group_by(Event.event_date, Platform.code).all()
    return {(event_date, code): int(count) for event_date, code, count in rows}


# График «Личные рекорды по системам» удалён с главной (16.07.2026, Дмитрий):
# is_pr у 5 вёрст подмешивает метку протокола «Личный рекорд!» (рекорд конкретной
# трассы, не хронологическое улучшение), цифры сильно завышены. Вернуть график
# можно после того, как трассовый рекорд переедет в отдельный маркер в модели —
# история реализации в git (v11–v15: варианты с is_pr и с оконной функцией).


def _total_time_sec(db: Session, excluded_location_ids: list[UUID]) -> int:
    return int(
        _exclude_locations(
            _not_crosslink_secondary(
                db.query(func.coalesce(func.sum(RunResult.finish_time_sec), 0))
                .select_from(RunResult)
                .join(Event, Event.id == RunResult.event_id)
            ),
            excluded_location_ids,
        ).scalar()
        or 0
    )


def _avg_finish_sec_in_range(
    db: Session,
    excluded_location_ids: list[UUID],
    start: date,
    end: date,
) -> int | None:
    row = _exclude_locations(
        _not_crosslink_secondary(
            db.query(
                func.count(RunResult.id),
                func.coalesce(func.sum(RunResult.finish_time_sec), 0),
            )
            .select_from(RunResult)
            .join(Event, Event.id == RunResult.event_id)
        ).filter(
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec >= MIN_SANE_FINISH_SEC,
            Event.event_date >= start,
            Event.event_date <= end,
        ),
        excluded_location_ids,
    ).one()
    count, total = row
    return int(total) // int(count) if count else None


def _total_time_sec_in_range(
    db: Session,
    excluded_location_ids: list[UUID],
    start: date,
    end: date,
) -> int:
    return int(
        _exclude_locations(
            _not_crosslink_secondary(
                db.query(func.coalesce(func.sum(RunResult.finish_time_sec), 0))
                .select_from(RunResult)
                .join(Event, Event.id == RunResult.event_id)
            ).filter(
                Event.event_date >= start,
                Event.event_date <= end,
            ),
            excluded_location_ids,
        ).scalar()
        or 0
    )


def _compute_portal_home(db: Session) -> dict[str, Any]:
    resolver = LocationCatalogIndex(db)
    locations = {loc.id: loc for loc in db.query(Location).all()}
    platform_code_by_location = {
        loc.id: code
        for loc, code in db.query(Location, Platform.code).join(
            Platform, Platform.id == Location.platform_id
        )
    }

    def identity_of(location_id: UUID) -> str:
        location = locations[location_id]
        return resolver.canonical_identity_key(
            location, platform_code_by_location[location_id]
        )

    def display_of(location_id: UUID) -> str:
        location = locations[location_id]
        return resolver.display_name(location, platform_code_by_location[location_id])

    def is_foreign_parkrun(location_id: UUID) -> bool:
        """Зарубежные parkrun-площадки из синка профилей: финиши учитываем,
        но в счёт локаций/географии/карточек систем не берём. Русские
        legacy-паркраны связаны с 5в/S95 через каталог — по этому и отличаем."""
        if platform_code_by_location[location_id] != "parkrun":
            return False
        location = locations[location_id]
        return resolver.get_for_location(location, "parkrun") is None

    events = _load_events(db)
    # Зарубежные parkrun-старты участников ломают общую статистику —
    # исключаем их из ВСЕХ блоков главной (решение 13.07.2026).
    excluded_location_ids = [
        location_id for location_id in locations if is_foreign_parkrun(location_id)
    ]
    excluded_set = set(excluded_location_ids)
    events = [row for row in events if row.location_id not in excluded_set]

    # --- герой ---
    starts_total = len(events)
    finishes_total = sum(row.finishers for row in events)
    identity_keys = {identity_of(row.location_id) for row in events}
    hero = {
        "finishes_total": finishes_total,
        "participants_total": _participants_total(db),
        "locations_total": len(identity_keys),
        "starts_total": starts_total,
    }

    # --- пульс последней субботы (последняя СУББОТА с данными: на проде
    # бывают переносы/зарубежные старты в другие дни — они не «пульс») ---
    pulse: dict[str, Any] | None = None
    pulse_date: date | None = max(
        (
            row.event_date
            for row in events
            if row.finishers > 0 and row.event_date.weekday() == 5
        ),
        default=None,
    )
    if pulse_date is not None:
        day_events = [row for row in events if row.event_date == pulse_date]
        newcomers, personal_records = _pulse_flags(db, pulse_date, excluded_location_ids)
        pulse = {
            "event_date": pulse_date,
            "starts": len(day_events),
            "finishes": sum(row.finishers for row in day_events),
            "newcomers": newcomers,
            "volunteers": _pulse_volunteers(db, pulse_date, excluded_location_ids),
            "personal_records": personal_records,
        }

    # --- герой за последний год (окно 52 недели, как недельный график) ---
    hero_last_year: dict[str, Any] | None = None
    if pulse_date is not None:
        year_start = pulse_date - timedelta(days=7 * 52 - 1)
        year_events = [row for row in events if year_start <= row.event_date <= pulse_date]
        hero_last_year = {
            "finishes_total": sum(row.finishers for row in year_events),
            "participants_total": _participants_in_window(
                db, year_start, pulse_date, excluded_location_ids
            ),
            "locations_total": len(
                {identity_of(row.location_id) for row in year_events}
            ),
            "starts_total": len(year_events),
        }

    # --- топ локаций последней субботы ---
    top_saturday: list[dict[str, Any]] = []
    if pulse_date is not None:
        best_by_identity: dict[str, _EventRow] = {}
        for row in events:
            if row.event_date != pulse_date or row.finishers <= 0:
                continue
            key = identity_of(row.location_id)
            best_row = best_by_identity.get(key)
            if best_row is None or row.finishers > best_row.finishers:
                best_by_identity[key] = row
        top_rows = sorted(
            best_by_identity.values(), key=lambda row: row.finishers, reverse=True
        )[:10]
        top_saturday = [
            {
                "location_name": display_of(row.location_id),
                "platform_code": row.platform_code,
                "finishers": row.finishers,
            }
            for row in top_rows
        ]

    # --- рекорды недели ---
    week_records: dict[str, Any] | None = None
    if pulse_date is not None:
        week_end = pulse_date
        week_start = week_end - timedelta(days=WEEK_RECORD_WINDOW_DAYS - 1)

        # посещаемость: исторический максимум по физической локации до недели
        attendance_before: dict[str, tuple[int, date]] = {}
        for row in events:
            if row.event_date < week_start and row.finishers > 0:
                key = identity_of(row.location_id)
                known = attendance_before.get(key)
                if known is None or row.finishers > known[0]:
                    attendance_before[key] = (row.finishers, row.event_date)
        attendance_records: dict[str, dict[str, Any]] = {}
        for row in events:
            if not (week_start <= row.event_date <= week_end) or row.finishers <= 0:
                continue
            key = identity_of(row.location_id)
            known = attendance_before.get(key)
            if known is None or row.finishers <= known[0]:
                continue
            existing_attendance = attendance_records.get(key)
            if existing_attendance is not None and existing_attendance["finishers"] >= row.finishers:
                continue
            attendance_records[key] = {
                "location_name": display_of(row.location_id),
                "platform_code": row.platform_code,
                "event_date": row.event_date,
                "finishers": row.finishers,
                "previous_record": known[0],
                "previous_record_date": known[1],
            }
        attendance_list = sorted(
            attendance_records.values(),
            key=lambda item: item["finishers"] - item["previous_record"],
            reverse=True,
        )[:WEEK_RECORDS_LIMIT]

        # рекорды трассы М/Ж: лучшее время недели против исторического
        # минимума физической локации
        # Рекорды трассы считаются ВНУТРИ системы (Дмитрий, 13.07.2026):
        # 5в-рекорд локации не смешивается с её же parkrun-эрой — ключ по
        # конкретному location_id (уникален в рамках платформы), не по
        # сквозной identity, которая как раз объединяет разные системы.
        course_before: dict[tuple[UUID, str], tuple[int, date]] = {}
        for location_id, gender, packed in _course_minima_before(db, week_start):
            if gender not in ("male", "female") or location_id in excluded_set:
                continue
            unpacked = _unpack_min(packed)
            if unpacked is None:
                continue
            course_key = (location_id, gender)
            known_min = course_before.get(course_key)
            if known_min is None or unpacked[0] < known_min[0]:
                course_before[course_key] = unpacked
        course_records: dict[tuple[UUID, str], dict[str, Any]] = {}
        for (
            _event_id,
            location_id,
            platform_code,
            event_date,
            gender,
            finish_sec,
            finish_display,
            runner_name,
        ) in _week_results(db, week_start, week_end, excluded_location_ids):
            if gender not in ("male", "female"):
                continue
            course_key = (location_id, gender)
            previous_min = course_before.get(course_key)
            if previous_min is None or finish_sec >= previous_min[0]:
                continue
            existing_course = course_records.get(course_key)
            if existing_course is not None and existing_course["_finish_sec"] <= finish_sec:
                continue
            course_records[course_key] = {
                "_finish_sec": finish_sec,
                "location_name": display_of(location_id),
                "platform_code": platform_code,
                "event_date": event_date,
                "gender": gender,
                "time_display": clean_time_display(finish_display, finish_sec),
                "runner_name": runner_name,
                "previous_display": format_finish_time(previous_min[0]),
                "previous_record_date": previous_min[1],
                "delta_sec": previous_min[0] - finish_sec,
            }
        course_list = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in sorted(
                course_records.values(),
                key=lambda item: (
                    str(item["location_name"]),
                    0 if item["gender"] == "male" else 1,
                ),
            )[:WEEK_RECORDS_LIMIT]
        ]
        week_records = {
            "week_start": week_start,
            "week_end": week_end,
            "attendance": attendance_list,
            "course": course_list,
        }

    # --- графики: финиши по годам/неделям в разбивке по системам ---
    finishes_by_year: dict[int, int] = defaultdict(int)
    finishes_year_platform: dict[tuple[int, str], int] = defaultdict(int)
    locations_year_platform: dict[tuple[int, str], set[str]] = defaultdict(set)
    for row in events:
        year = row.event_date.year
        finishes_by_year[year] += row.finishers
        finishes_year_platform[(year, row.platform_code)] += row.finishers
        locations_year_platform[(year, row.platform_code)].add(
            identity_of(row.location_id)
        )
    sorted_years = sorted(finishes_by_year)
    while sorted_years and finishes_by_year[sorted_years[0]] == 0:
        sorted_years.pop(0)

    def _year_platforms(year: int, source: dict[tuple[int, str], int]) -> dict[str, int]:
        return {
            code: source[(year, code)]
            for code in PLATFORM_ORDER
            if source.get((year, code))
        }

    chart_years = [
        {
            "year": year,
            "total": finishes_by_year[year],
            "platforms": _year_platforms(year, finishes_year_platform),
        }
        for year in sorted_years
    ]

    # --- новички по системам (годы) ---
    newcomers_year_platform = _newcomers_by_year(db, excluded_location_ids)

    # --- прогноз на конец текущего (неполного) года по темпу этого года ---
    # (Дмитрий, 13.07.2026: год ещё не закончился — честнее показать пунктиром,
    # сколько будет к концу года при среднем темпе, чем давать заниженную точку.
    # 14.07.2026: та же экстраполяция для новичков.)
    def _year_projection(
        source: dict[tuple[int, str], int], current_year: int, weeks_elapsed: int
    ) -> dict[str, Any]:
        current_platforms = _year_platforms(current_year, source)
        projected_platforms = {
            code: round(value / weeks_elapsed * 52) for code, value in current_platforms.items()
        }
        return {
            "year": current_year,
            "weeks_elapsed": weeks_elapsed,
            "total": sum(projected_platforms.values()),
            "platforms": projected_platforms,
        }

    chart_year_projection: dict[str, Any] | None = None
    newcomers_year_projection: dict[str, Any] | None = None
    if sorted_years and pulse_date is not None and sorted_years[-1] == pulse_date.year:
        current_year = sorted_years[-1]
        weeks_elapsed = max(1, pulse_date.isocalendar()[1])
        if weeks_elapsed < 52:
            chart_year_projection = _year_projection(
                finishes_year_platform, current_year, weeks_elapsed
            )
            newcomers_year_projection = _year_projection(
                newcomers_year_platform, current_year, weeks_elapsed
            )

    locations_by_year = [
        {
            "year": year,
            "platforms": {
                code: len(locations_year_platform[(year, code)])
                for code in PLATFORM_ORDER
                if locations_year_platform.get((year, code))
            },
        }
        for year in sorted_years
    ]

    newcomers_by_year = [
        {
            "year": year,
            "total": sum(newcomers_year_platform.get((year, code), 0) for code in PLATFORM_ORDER),
            "platforms": _year_platforms(year, newcomers_year_platform),
        }
        for year in sorted_years
    ]

    chart_weeks: list[dict[str, Any]] = []
    locations_by_week: list[dict[str, Any]] = []
    newcomers_by_week: list[dict[str, Any]] = []
    if pulse_date is not None:
        weeks_start = pulse_date - timedelta(days=7 * 52 - 1)
        finishes_by_week: dict[date, int] = defaultdict(int)
        finishes_week_platform: dict[tuple[date, str], int] = defaultdict(int)
        locations_week_platform: dict[tuple[date, str], set[str]] = defaultdict(set)
        for row in events:
            if row.event_date < weeks_start or row.event_date > pulse_date:
                continue
            week_key = row.event_date - timedelta(days=row.event_date.weekday())
            finishes_by_week[week_key] += row.finishers
            finishes_week_platform[(week_key, row.platform_code)] += row.finishers
            locations_week_platform[(week_key, row.platform_code)].add(
                identity_of(row.location_id)
            )
        chart_weeks = [
            {
                "week_start": week,
                "total": finishes_by_week[week],
                "platforms": {
                    code: finishes_week_platform[(week, code)]
                    for code in PLATFORM_ORDER
                    if finishes_week_platform.get((week, code))
                },
            }
            for week in sorted(finishes_by_week)
        ]
        locations_by_week = [
            {
                "week_start": week,
                "platforms": {
                    code: len(locations_week_platform[(week, code)])
                    for code in PLATFORM_ORDER
                    if locations_week_platform.get((week, code))
                },
            }
            for week in sorted(finishes_by_week)
        ]

        # --- новички по системам (последние 52 недели) ---
        newcomers_by_date = _newcomers_by_date(db, excluded_location_ids, weeks_start, pulse_date)
        newcomers_week_platform: dict[tuple[date, str], int] = defaultdict(int)
        for (event_date, platform_code), count in newcomers_by_date.items():
            week_key = event_date - timedelta(days=event_date.weekday())
            newcomers_week_platform[(week_key, platform_code)] += count
        newcomers_by_week = [
            {
                "week_start": week,
                "total": sum(newcomers_week_platform[(week, code)] for code in PLATFORM_ORDER),
                "platforms": {
                    code: newcomers_week_platform[(week, code)]
                    for code in PLATFORM_ORDER
                    if newcomers_week_platform.get((week, code))
                },
            }
            for week in sorted(finishes_by_week)
        ]

    # --- карточки систем ---
    avg_overall, avg_by_platform = _avg_finish_by_platform(db, excluded_location_ids)
    gender_total, gender_by_platform = _gender_split(db, excluded_location_ids)
    recent_cutoff = (
        pulse_date - timedelta(days=60) if pulse_date is not None else None
    )
    systems: list[dict[str, Any]] = []
    by_platform: dict[str, list[_EventRow]] = defaultdict(list)
    for row in events:
        by_platform[row.platform_code].append(row)
    for code in PLATFORM_ORDER:
        rows = by_platform.get(code)
        if not rows:
            continue
        # годы считаем по событиям с финишёрами: у архивных систем в БД
        # встречаются пустые события-заглушки с датами вне реальной эпохи
        years = [row.event_date.year for row in rows if row.finishers > 0]
        if not years:
            years = [row.event_date.year for row in rows]
        is_active = code in ACTIVE_PLATFORM_CODES
        if is_active and recent_cutoff is not None:
            # у действующих систем показываем ДЕЙСТВУЮЩИЕ локации:
            # был старт за последние 60 дней
            locations_count = len(
                {
                    identity_of(row.location_id)
                    for row in rows
                    if row.event_date >= recent_cutoff
                }
            )
        else:
            locations_count = len({identity_of(row.location_id) for row in rows})
        avg_sec = avg_by_platform.get(code)
        systems.append(
            {
                "code": code,
                "title": PLATFORM_TITLES.get(code, code),
                "locations": locations_count,
                "finishes": sum(row.finishers for row in rows),
                "first_year": min(years),
                "last_year": max(years),
                "is_active": is_active,
                "avg_finish_display": format_finish_time(avg_sec) if avg_sec else None,
            }
        )

    # --- география ---
    starts_by_identity: dict[str, int] = defaultdict(int)
    identity_sample_location: dict[str, UUID] = {}
    for row in events:
        key = identity_of(row.location_id)
        starts_by_identity[key] += 1
        identity_sample_location.setdefault(key, row.location_id)
    points: list[dict[str, Any]] = []
    regions: set[str] = set()
    for key, location_id in identity_sample_location.items():
        location = locations[location_id]
        if location.region:
            regions.add(location.region)
        latitude, longitude = resolver.coordinates_for(
            location, platform_code_by_location[location_id]
        )
        if latitude is None or longitude is None:
            latitude, longitude = location.latitude, location.longitude
        if latitude is None or longitude is None:
            continue
        points.append(
            {
                "latitude": latitude,
                "longitude": longitude,
                "starts": starts_by_identity[key],
                "location_name": display_of(location_id),
                "platform_code": platform_code_by_location[location_id],
                "region": location.region,
            }
        )
    points.sort(key=lambda point: point["starts"], reverse=True)
    geo = {
        "locations_total": len(identity_keys),
        "regions_total": len(regions),
        "points": points[:GEO_POINTS_LIMIT],
    }

    # --- рекорды посещаемости: топ-10 локаций за всю историю и за текущий год ---
    attendance_year = pulse_date.year if pulse_date is not None else None

    def _attendance_top(year: int | None) -> list[dict[str, Any]]:
        best: dict[str, _EventRow] = {}
        for row in events:
            if row.finishers <= 0:
                continue
            if year is not None and row.event_date.year != year:
                continue
            key = identity_of(row.location_id)
            known = best.get(key)
            if known is None or row.finishers > known.finishers:
                best[key] = row
        top = sorted(best.values(), key=lambda row: row.finishers, reverse=True)[:10]
        return [
            {
                "location_name": display_of(row.location_id),
                "platform_code": row.platform_code,
                "event_date": row.event_date,
                "finishers": row.finishers,
            }
            for row in top
        ]

    attendance_top_all = _attendance_top(None)
    attendance_top_year = _attendance_top(attendance_year)

    # --- самый массовый день: общий и по каждой системе ---
    finishes_by_date: dict[date, int] = defaultdict(int)
    finishes_by_date_platform: dict[tuple[date, str], int] = defaultdict(int)
    for row in events:
        finishes_by_date[row.event_date] += row.finishers
        finishes_by_date_platform[(row.event_date, row.platform_code)] += row.finishers

    busiest_day_overall: dict[str, Any] | None = None
    if finishes_by_date:
        best_date = max(finishes_by_date, key=lambda d: finishes_by_date[d])
        busiest_day_overall = {
            "event_date": best_date,
            "finishers": finishes_by_date[best_date],
        }

    busiest_day_by_platform: list[dict[str, Any]] = []
    for code in PLATFORM_ORDER:
        dates_for_code = {
            d: total
            for (d, platform_code), total in finishes_by_date_platform.items()
            if platform_code == code
        }
        if not dates_for_code:
            continue
        best_date = max(dates_for_code, key=lambda d: dates_for_code[d])
        busiest_day_by_platform.append(
            {
                "platform_code": code,
                "event_date": best_date,
                "finishers": dates_for_code[best_date],
            }
        )

    fresh_since = (
        pulse_date - timedelta(days=WEEK_RECORD_WINDOW_DAYS - 1)
        if pulse_date is not None
        else None
    )
    fastest = []
    for item in _fastest_by_platform(db, excluded_location_ids, fresh_since):
        location_id = item.pop("_location_id")
        item["location_name"] = display_of(location_id)
        fastest.append(item)

    # --- недельные дельты для факт-плашек (Дмитрий, 13.07.2026) ---
    distance_delta_km: int | None = None
    time_delta_sec: int | None = None
    avg_delta_sec: int | None = None
    if pulse_date is not None:
        fw_end = pulse_date
        fw_start = fw_end - timedelta(days=WEEK_RECORD_WINDOW_DAYS - 1)
        prev_end = fw_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=WEEK_RECORD_WINDOW_DAYS - 1)
        finishes_this_week = sum(
            row.finishers for row in events if fw_start <= row.event_date <= fw_end
        )
        distance_delta_km = finishes_this_week * DISTANCE_KM
        time_delta_sec = _total_time_sec_in_range(
            db, excluded_location_ids, fw_start, fw_end
        )
        avg_this_week = _avg_finish_sec_in_range(db, excluded_location_ids, fw_start, fw_end)
        avg_prev_week = _avg_finish_sec_in_range(
            db, excluded_location_ids, prev_start, prev_end
        )
        if avg_this_week is not None and avg_prev_week is not None:
            avg_delta_sec = avg_this_week - avg_prev_week

    fun_facts = {
        "total_distance_km": finishes_total * DISTANCE_KM,
        "total_time_sec": _total_time_sec(db, excluded_location_ids),
        "avg_finish_display": format_finish_time(avg_overall) if avg_overall else None,
        "distance_delta_km": distance_delta_km,
        "time_delta_sec": time_delta_sec,
        "avg_delta_sec": avg_delta_sec,
    }

    return {
        "generated_at": datetime.now(timezone.utc),
        "hero": hero,
        "hero_last_year": hero_last_year,
        "fastest": fastest,
        "week_records": week_records,
        "top_saturday": top_saturday,
        "pulse": pulse,
        "chart_years": chart_years,
        "chart_year_projection": chart_year_projection,
        "chart_weeks": chart_weeks,
        "locations_by_year": locations_by_year,
        "locations_by_week": locations_by_week,
        "newcomers_by_year": newcomers_by_year,
        "newcomers_by_week": newcomers_by_week,
        "newcomers_year_projection": newcomers_year_projection,
        "attendance_year": attendance_year,
        "attendance_top_all": attendance_top_all,
        "attendance_top_year": attendance_top_year,
        "busiest_day_overall": busiest_day_overall,
        "busiest_day_by_platform": busiest_day_by_platform,
        "systems": systems,
        "geo": geo,
        "fun_facts": fun_facts,
        "gender_split": {
            "total": gender_total,
            "by_platform": [
                {
                    "platform_code": code,
                    "male": gender_by_platform[code]["male"],
                    "female": gender_by_platform[code]["female"],
                }
                for code in PLATFORM_ORDER
                if code in gender_by_platform
                and (gender_by_platform[code]["male"] + gender_by_platform[code]["female"]) > 0
            ],
        },
    }


def _read_portal_home_cache() -> dict[str, Any] | None:
    try:
        raw = get_redis_client().get(PORTAL_HOME_CACHE_KEY)
    except redis.RedisError:
        logger.warning("portal home cache read failed", exc_info=True)
        return None
    if not raw or not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_portal_home_cache(payload: dict[str, Any]) -> None:
    try:
        get_redis_client().set(
            PORTAL_HOME_CACHE_KEY,
            json.dumps(payload, default=str),
            ex=PORTAL_HOME_CACHE_TTL_SECONDS,
        )
    except redis.RedisError:
        logger.warning("portal home cache write failed", exc_info=True)


def invalidate_portal_home_cache() -> None:
    try:
        get_redis_client().delete(PORTAL_HOME_CACHE_KEY)
    except redis.RedisError:
        logger.warning("portal home cache invalidate failed", exc_info=True)


def warm_portal_home_cache(db: Session) -> None:
    """Принудительный пересчёт и запись в кэш — для планового прогрева по
    расписанию (celery beat), чтобы пользователь никогда не попадал на
    холодный агрегат после истечения TTL."""
    payload = _compute_portal_home(db)
    _write_portal_home_cache(payload)


def build_portal_home(db: Session, use_cache: bool = True) -> PortalHomeResponse:
    if use_cache:
        cached = _read_portal_home_cache()
        if cached is not None:
            return PortalHomeResponse.model_validate(cached)
    payload = _compute_portal_home(db)
    response = PortalHomeResponse.model_validate(payload)
    if use_cache:
        _write_portal_home_cache(payload)
    return response
