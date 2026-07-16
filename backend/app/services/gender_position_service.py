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
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Participant, RunResult

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


def recalculate_event_gender_positions(db: Session, event_id: UUID, platform_code: str) -> None:
    """Пересчитать gender_position для всех результатов одного события."""
    if platform_code not in ("five_verst", "s95", "runpark", "parkrun"):
        return
    rows = db.query(RunResult).filter(RunResult.event_id == event_id).all()
    if not rows:
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
