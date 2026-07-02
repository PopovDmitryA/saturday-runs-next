"""Позиция в протоколе среди финишёров того же пола.

Источник пола по платформам:
- five_verst: первая буква age_category результата («М» / «Ж»);
- s95: в протоколе категории нет, пол берётся из participants.profile_extra
  ["platform_codes"]["gender"] («male» / «female»), который сохраняет
  _store_athlete_codes при апсерте JSON-протокола;
- parkrun: данных о поле нет, gender_position всегда NULL;
- runpark: категория (VM35-39/SW30-34) есть только у 41% строк — слишком
  неполные протоколы, место по полу не считаем (NULL).

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


def gender_from_age_category(platform_code: str, age_category: str | None) -> str | None:
    if not age_category:
        return None
    if platform_code == "five_verst":
        return _FIVE_VERST_LETTERS.get(age_category[:1])
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


def recalculate_event_gender_positions(db: Session, event_id: UUID, platform_code: str) -> None:
    """Пересчитать gender_position для всех результатов одного события."""
    if platform_code not in ("five_verst", "s95"):
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
