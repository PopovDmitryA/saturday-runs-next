"""Позиция в протоколе среди финишёров того же пола.

Источник пола по платформам:
- five_verst: первая буква age_category результата («М» / «Ж»);
- s95: в протоколе категории нет, пол берётся из participants.profile_extra
  ["platform_codes"]["gender"] («male» / «female»), который сохраняет
  _store_athlete_codes при апсерте JSON-протокола;
- runpark: вторая буква age_category («VM35-39» / «SW30-34» → M / W);
  категория есть только у ~41% строк, так что метрика считается с
  погрешностью — только среди финишёров с известным полом;
- parkrun: в run_results.age_category лежит age-grade % (не категория!),
  своей категории протокол не даёт — пол берётся из participants.age_category
  («SM30-34» — вторая буква; см. location_page_service._gender_expression,
  тот же источник). Появилось после бэкфилла профилей parkrun; до него тут
  было «данных о поле нет, gender_position всегда NULL».

Бегуны без известного пола не участвуют в ранжировании (место считается
только среди тех, чей пол известен).

Отдельное правило для parkrun: место по полу считается ТОЛЬКО на русских
площадках (~165 строк локаций, связанных с каталогом). Протоколы зарубежного
parkrun мы не собираем — от такой площадки в БД есть лишь строки наших же
участников из их профилей, и «первая среди женщин» получалась у каждой
женщины на каждом зарубежном старте (см. is_foreign_parkrun_event).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Event, Participant, RunResult
from app.services.location_catalog_service import russian_parkrun_location_ids

GENDER_MALE = "male"
GENDER_FEMALE = "female"

_FIVE_VERST_LETTERS = {"М": GENDER_MALE, "Ж": GENDER_FEMALE}
_RUNPARK_LETTERS = {"M": GENDER_MALE, "W": GENDER_FEMALE}


def gender_from_age_category(platform_code: str, age_category: str | None) -> str | None:
    if not age_category:
        return None
    if platform_code == "five_verst":
        return _FIVE_VERST_LETTERS.get(age_category[:1])
    if platform_code == "runpark":
        if len(age_category) >= 2:
            return _RUNPARK_LETTERS.get(age_category[1])
        return None
    return None


def resolve_participant_gender(
    platform_code: str,
    age_category: str | None,
    profile_extra: dict | None = None,
) -> str | None:
    """Пол участника из тех же источников, что и выше, но по данным одной строки.

    Нужна на записи: с 24.07.2026 пол хранится в participants.gender, чтобы не
    вычислять его из строки категории на каждом чтении (агрегаты главной гоняли
    substr по 2.2 млн строк — выражение неиндексируемое, отсюда полный перебор
    и спилл сортировок на диск).
    """
    if platform_code == "s95":
        gender = ((profile_extra or {}).get("platform_codes") or {}).get("gender")
        return gender if gender in (GENDER_MALE, GENDER_FEMALE) else None
    if platform_code in ("runpark", "parkrun"):
        # У обеих платформ формат один: «SM30-34» / «VW35-39» — пол во второй букве.
        return gender_from_age_category("runpark", age_category)
    return gender_from_age_category(platform_code, age_category)


def _s95_participant_genders(db: Session, participant_ids: list[UUID]) -> dict[UUID, str]:
    if not participant_ids:
        return {}
    rows = (
        db.query(Participant.id, Participant.profile_extra)
        .filter(Participant.id.in_(participant_ids))
        .all()
    )
    result: dict[UUID, str] = {}
    for pid, extra in rows:
        gender = ((extra or {}).get("platform_codes") or {}).get("gender")
        if gender in (GENDER_MALE, GENDER_FEMALE):
            result[pid] = gender
    return result


def _parkrun_participant_genders(db: Session, participant_ids: list[UUID]) -> dict[UUID, str]:
    """Пол по participants.age_category («SM30-34» → вторая буква M/W)."""
    if not participant_ids:
        return {}
    rows = (
        db.query(Participant.id, Participant.age_category)
        .filter(Participant.id.in_(participant_ids))
        .all()
    )
    result: dict[UUID, str] = {}
    for pid, age_category in rows:
        gender = gender_from_age_category("runpark", age_category)  # тот же формат: 2-я буква M/W
        if gender is not None:
            result[pid] = gender
    return result


def is_foreign_parkrun_event(db: Session, event_id: UUID) -> bool:
    """Событие зарубежного parkrun — того, чей протокол мы не видим.

    От такой площадки в БД лежат только строки наших же участников, попавшие
    туда из их профилей: место среди своего пола считать не по чему (в «поле» из
    одной строки любой финишёр первый). Русские parkrun-площадки — те, что
    связаны с каталогом локаций (см. russian_parkrun_location_ids).
    """
    location_id = (
        db.query(Event.location_id).filter(Event.id == event_id).scalar()
    )
    if location_id is None:
        return True
    return location_id not in russian_parkrun_location_ids(db)


def _clear_event_gender_positions(db: Session, rows: list[RunResult]) -> None:
    changed = False
    for row in rows:
        if row.gender_position is not None:
            row.gender_position = None
            changed = True
    if changed:
        db.flush()


def recalculate_event_gender_positions(db: Session, event_id: UUID, platform_code: str) -> None:
    """Пересчитать gender_position для всех результатов одного события."""
    if platform_code not in ("five_verst", "s95", "runpark", "parkrun"):
        return
    rows = db.query(RunResult).filter(RunResult.event_id == event_id).all()
    if not rows:
        return

    # Зарубежный parkrun: места по полу нет вовсе (решение Дмитрия 01.08.2026).
    if platform_code == "parkrun" and is_foreign_parkrun_event(db, event_id):
        _clear_event_gender_positions(db, rows)
        return

    genders: dict[UUID, str | None] = {}
    if platform_code == "s95":
        participant_genders = _s95_participant_genders(
            db, [row.participant_id for row in rows if row.participant_id is not None]
        )
        for row in rows:
            genders[row.id] = participant_genders.get(row.participant_id) if row.participant_id else None
    elif platform_code == "parkrun":
        participant_genders = _parkrun_participant_genders(
            db, [row.participant_id for row in rows if row.participant_id is not None]
        )
        for row in rows:
            genders[row.id] = participant_genders.get(row.participant_id) if row.participant_id else None
    else:
        for row in rows:
            genders[row.id] = gender_from_age_category(platform_code, row.age_category)

    ranked = sorted(
        (row for row in rows if genders[row.id] is not None and row.position is not None),
        key=lambda row: row.position,  # type: ignore[arg-type, return-value]
    )
    counters = {GENDER_MALE: 0, GENDER_FEMALE: 0}
    new_values: dict[UUID, int] = {}
    for row in ranked:
        gender = genders[row.id]
        counters[gender] += 1  # type: ignore[index]
        new_values[row.id] = counters[gender]  # type: ignore[index]

    changed = False
    for row in rows:
        value = new_values.get(row.id)
        if row.gender_position != value:
            row.gender_position = value
            changed = True
    if changed:
        db.flush()
