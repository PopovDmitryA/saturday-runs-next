"""Открытия локаций: какой старт площадки считать первым.

Рейтинг открытий («Первопроходцы», Р16) считает участие в самом первом старте
площадки. Откуда берётся номер такого старта:

- 5 вёрст, parkrun, RunPark — из протокола: событие №1. У 5 вёрст перед ним
  бывает тестовый забег №0, но он помечен `is_test_event` и в рейтинги не идёт
  вовсе;
- С95 — руками через админку: номера забегов С95 не публикует, у нас это ранг
  в хронологии `/events/{slug}.json` (см. project_s95_event_numbering), и
  совпадение с «торжественным открытием» ничем не гарантировано. Пока номер не
  проставлен, открытий у площадки С95 нет.

Ручная разметка (таблица `location_openings`) главнее номера из протокола и для
остальных систем тоже: строка с пустым номером гасит открытие там, где система
начала вести протоколы позже самой площадки (RunPark подхватывал площадки,
давно работавшие на 5 вёрст, и его событие №1 открытием не было).

Разметка привязана к локации ПЛАТФОРМЫ, а не к физической точке: площадка,
открывшаяся сначала в parkrun, а потом в 5 вёрст, открывалась дважды, и оба
старта — открытия (решение Дмитрия 14.08.2026).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, text, tuple_
from sqlalchemy.orm import Session

from app.models import Event, Location, LocationOpening, Platform, RunResult, User

# Системы, где открытие видно из протокола: событие №1 и есть первый старт.
AUTO_OPENING_PLATFORMS: tuple[str, ...] = ("five_verst", "parkrun", "runpark")
AUTO_OPENING_NUMBER = 1
# С95: номер открытия проставляется руками, по умолчанию открытия нет.
MANUAL_OPENING_PLATFORMS: tuple[str, ...] = ("s95",)
# Порядок как на портале: активные системы, затем архив parkrun.
OPENING_PLATFORMS: tuple[str, ...] = ("five_verst", "s95", "runpark", "parkrun")

# Сколько первых стартов площадки показываем в админке подсказкой: по дате и
# числу финишёров сразу видно, какой из них был открытием.
FIRST_EVENTS_PREVIEW = 6

# Условие «это событие — открытие площадки» для сырых выборок рейтинга.
# Ждёт в запросе алиасы `e` (events) и `p` (platforms) и join из
# OPENING_EVENT_JOIN. Ручная строка главнее протокола, причём её пустой номер —
# это осознанное «открытия нет»: сравнение с NULL даёт NULL, и событие в зачёт
# не идёт. Площадка системы без ручной разметки и без автоправила (С95) тоже
# остаётся без открытия — по той же причине.
OPENING_EVENT_JOIN = "LEFT JOIN location_openings lo ON lo.location_id = e.location_id"
_AUTO_PLATFORMS_SQL = ", ".join(f"'{code}'" for code in AUTO_OPENING_PLATFORMS)
OPENING_EVENT_CONDITION = f"""e.event_number = CASE
    WHEN lo.location_id IS NOT NULL THEN lo.opening_event_number
    WHEN p.code IN ({_AUTO_PLATFORMS_SQL}) THEN {AUTO_OPENING_NUMBER}
  END"""


class LocationOpeningError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def resolve_opening_number(platform_code: str, override: LocationOpening | None) -> int | None:
    """Номер старта-открытия для локации: ручная разметка или правило системы.

    None — открытия у площадки нет (не размечено у С95 либо погашено вручную).
    """
    if override is not None:
        return override.opening_event_number
    if platform_code in AUTO_OPENING_PLATFORMS:
        return AUTO_OPENING_NUMBER
    return None


def _event_payload(
    event: Event, finishers: int | None, *, is_opening: bool = False
) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "event_number": event.event_number,
        "event_date": event.event_date,
        "title": event.title,
        "source_url": event.source_url,
        # У parkrun и С95 finishers_count в протоколе не приходит — считаем
        # строки протокола сами, иначе подсказка админке пустая ровно там, где
        # она нужнее всего (номер открытия С95 проставляется по ней).
        "finishers": finishers if finishers is not None else event.finishers_count,
        "is_opening": is_opening,
    }


def _finishers_by_event(db: Session, event_ids: list[UUID]) -> dict[UUID, int]:
    if not event_ids:
        return {}
    rows = (
        db.query(RunResult.event_id, func.count(RunResult.id))
        .filter(RunResult.event_id.in_(event_ids))
        .group_by(RunResult.event_id)
        .all()
    )
    return {row[0]: int(row[1]) for row in rows}


def _first_events(db: Session, location_ids: list[UUID]) -> dict[UUID, list[Event]]:
    """Первые старты каждой площадки — подсказка «какой из них открытие»."""
    if not location_ids:
        return {}
    ranked = (
        db.query(
            Event.id.label("event_id"),
            func.row_number()
            .over(
                partition_by=Event.location_id,
                order_by=(Event.event_number.asc().nullslast(), Event.event_date.asc()),
            )
            .label("rn"),
        )
        .filter(Event.location_id.in_(location_ids), Event.is_test_event.is_(False))
        .subquery()
    )
    events = (
        db.query(Event)
        .join(ranked, ranked.c.event_id == Event.id)
        .filter(ranked.c.rn <= FIRST_EVENTS_PREVIEW)
        .all()
    )
    by_location: dict[UUID, list[Event]] = {}
    for event in events:
        by_location.setdefault(event.location_id, []).append(event)
    for group in by_location.values():
        group.sort(key=lambda e: (e.event_number is None, e.event_number or 0, e.event_date))
    return by_location


def _opening_events(
    db: Session, wanted: list[tuple[UUID, int]]
) -> dict[tuple[UUID, int], Event]:
    """События по парам (локация, номер): ручной номер бывает и вне превью."""
    if not wanted:
        return {}
    events = (
        db.query(Event)
        .filter(tuple_(Event.location_id, Event.event_number).in_(wanted))
        .all()
    )
    return {(event.location_id, int(event.event_number or 0)): event for event in events}


def list_openings(
    db: Session,
    *,
    platform: str = "s95",
    query: str | None = None,
    only_missing: bool = False,
    limit: int = 300,
) -> dict[str, Any]:
    """Разметка открытий для админки: площадки системы и их первые старты."""
    if platform not in OPENING_PLATFORMS:
        raise LocationOpeningError("Неизвестная система")

    locations_query = (
        db.query(Location, LocationOpening)
        .join(Platform, Platform.id == Location.platform_id)
        .outerjoin(LocationOpening, LocationOpening.location_id == Location.id)
        .filter(Platform.code == platform)
    )
    if query:
        pattern = f"%{query.strip().lower()}%"
        locations_query = locations_query.filter(
            func.lower(Location.name).like(pattern)
            | func.lower(func.coalesce(Location.city, "")).like(pattern)
            | func.lower(Location.external_key).like(pattern)
        )
    rows = locations_query.order_by(Location.name).all()

    # «Без открытия» — те, где номер не определён вовсе: у С95 это ещё не
    # размеченные площадки, у остальных систем — погашенные вручную.
    resolved_numbers = {
        location.id: resolve_opening_number(platform, override) for location, override in rows
    }
    if only_missing:
        rows = [row for row in rows if resolved_numbers[row[0].id] is None]

    total = len(rows)
    rows = rows[: max(1, limit)]
    location_ids = [location.id for location, _override in rows]

    previews = _first_events(db, location_ids)
    wanted_openings: list[tuple[UUID, int]] = [
        (location.id, number)
        for location, _override in rows
        if (number := resolved_numbers[location.id]) is not None
    ]
    opening_events = _opening_events(db, wanted_openings)

    preview_ids = [event.id for group in previews.values() for event in group]
    opening_ids = [event.id for event in opening_events.values()]
    finishers = _finishers_by_event(db, list({*preview_ids, *opening_ids}))

    admins = {
        user.id: user.display_name
        for user in db.query(User)
        .filter(
            User.id.in_(
                [override.updated_by_user_id for _loc, override in rows if override is not None]
            )
        )
        .all()
    }

    items: list[dict[str, Any]] = []
    for location, override in rows:
        number = resolved_numbers[location.id]
        opening = opening_events.get((location.id, number)) if number is not None else None
        preview = previews.get(location.id, [])
        items.append(
            {
                "location_id": location.id,
                "location_name": location.name,
                "location_city": location.city,
                "external_key": location.external_key,
                "source_url": location.source_url,
                "platform_code": platform,
                "opening_event_number": number,
                # manual — номер задан руками (в т.ч. «открытия нет»);
                # auto — событие №1 из протокола; none — открытия у площадки нет.
                "opening_source": (
                    "manual" if override is not None else ("auto" if number is not None else "none")
                ),
                "opening_event": (
                    _event_payload(opening, finishers.get(opening.id), is_opening=True)
                    if opening is not None
                    else None
                ),
                # Номер задан, а события с ним нет — опечатка в разметке; в
                # рейтинге такая площадка молча не даёт открытия, поэтому
                # админка обязана показать это явно.
                "opening_event_missing": number is not None and opening is None,
                "note": override.note if override is not None else None,
                "updated_at": override.updated_at if override is not None else None,
                "updated_by": (
                    admins.get(override.updated_by_user_id) if override is not None else None
                ),
                "first_events": [
                    _event_payload(
                        event,
                        finishers.get(event.id),
                        is_opening=number is not None and event.event_number == number,
                    )
                    for event in preview
                ],
            }
        )

    return {
        "platform": platform,
        "items": items,
        "total": total,
        "with_opening": sum(1 for item in items if item["opening_event_number"] is not None),
        "manual_total": sum(1 for item in items if item["opening_source"] == "manual"),
        # Системе с автоправилом объяснять админке нечего, а С95 без разметки —
        # это «открытий пока нет», и счётчик заполненности главный на странице.
        "needs_manual": platform in MANUAL_OPENING_PLATFORMS,
    }


def set_opening(
    db: Session,
    location_id: UUID,
    *,
    opening_event_number: int | None,
    note: str | None,
    admin: User,
) -> dict[str, Any]:
    """Задать (или переписать) номер старта-открытия площадки.

    Пустой номер — это «открытия нет», а не «не знаю»: строка остаётся и гасит
    автоправило системы.
    """
    location = db.query(Location).filter(Location.id == location_id).one_or_none()
    if location is None:
        raise LocationOpeningError("Локация не найдена", status_code=404)
    if opening_event_number is not None and opening_event_number < 1:
        raise LocationOpeningError("Номер старта начинается с 1")

    override = (
        db.query(LocationOpening)
        .filter(LocationOpening.location_id == location_id)
        .one_or_none()
    )
    if override is None:
        override = LocationOpening(location_id=location_id)
        db.add(override)
    override.opening_event_number = opening_event_number
    override.note = (note or "").strip() or None
    override.updated_by_user_id = admin.id
    db.flush()
    db.commit()
    return _override_payload(db, location, override)


def clear_opening(db: Session, location_id: UUID) -> dict[str, Any]:
    """Убрать ручную разметку — площадка возвращается к правилу системы."""
    location = db.query(Location).filter(Location.id == location_id).one_or_none()
    if location is None:
        raise LocationOpeningError("Локация не найдена", status_code=404)
    override = (
        db.query(LocationOpening)
        .filter(LocationOpening.location_id == location_id)
        .one_or_none()
    )
    if override is not None:
        db.delete(override)
        db.flush()
        db.commit()
    return _override_payload(db, location, None)


def _override_payload(
    db: Session, location: Location, override: LocationOpening | None
) -> dict[str, Any]:
    platform_code = (
        db.query(Platform.code).filter(Platform.id == location.platform_id).scalar() or ""
    )
    number = resolve_opening_number(platform_code, override)
    opening = None
    if number is not None:
        # Номер берём как есть, а не one_or_none: в неполных данных (dev, свежий
        # синк С95) одна и та же нумерация иногда висит на нескольких датах, и
        # падать на этом админке незачем — показываем самый ранний старт.
        event = (
            db.query(Event)
            .filter(Event.location_id == location.id, Event.event_number == number)
            .order_by(Event.event_date)
            .first()
        )
        if event is not None:
            opening = _event_payload(
                event, _finishers_by_event(db, [event.id]).get(event.id), is_opening=True
            )
    return {
        "location_id": location.id,
        "location_name": location.name,
        "platform_code": platform_code,
        "opening_event_number": number,
        "opening_source": (
            "manual" if override is not None else ("auto" if number is not None else "none")
        ),
        "opening_event": opening,
        "opening_event_missing": number is not None and opening is None,
        "note": override.note if override is not None else None,
        "updated_at": override.updated_at if override is not None else None,
    }


_OPENINGS_COUNT_SQL = f"""
SELECT p.code, COUNT(*) AS openings
FROM events e
JOIN platforms p ON p.id = e.platform_id
{OPENING_EVENT_JOIN}
WHERE e.is_test_event = false
  AND {OPENING_EVENT_CONDITION}
GROUP BY p.code
"""


def opening_events_count(db: Session) -> dict[str, int]:
    """Сколько открытий знает каждая система — сводка для админки и тестов.

    Считается тем же условием, что и рейтинг (OPENING_EVENT_CONDITION), иначе
    цифра на странице разметки и цифра в рейтинге разошлись бы.
    """
    return {row[0]: int(row[1]) for row in db.execute(text(_OPENINGS_COUNT_SQL)).all()}


__all__ = [
    "AUTO_OPENING_NUMBER",
    "AUTO_OPENING_PLATFORMS",
    "MANUAL_OPENING_PLATFORMS",
    "OPENING_EVENT_CONDITION",
    "OPENING_EVENT_JOIN",
    "OPENING_PLATFORMS",
    "LocationOpeningError",
    "clear_opening",
    "list_openings",
    "opening_events_count",
    "resolve_opening_number",
    "set_opening",
]
