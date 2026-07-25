"""Тизер личной статистики на главной (гипотеза Т10 варианта B).

Анонимный посетитель вводит свой ID одной из систем — и видит предпросмотр
карточки с реальными агрегатами ИЗ НАШЕЙ БД (никаких живых фетчей внешних
сайтов: только то, что уже собрано синками). Полная статистика — за
регистрацией; тизер намеренно показывает мало.
"""

from __future__ import annotations

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Event, Participant, Platform, RunResult
from app.services.portal_home_service import format_finish_time

TEASER_PLATFORMS = ("five_verst", "s95", "parkrun", "runpark")

# ID во всех системах у нас цифровой; люди часто пишут parkrun-ID как "A331".
_ID_RE = re.compile(r"^[AaАа]?(\d{1,15})$")


def normalize_athlete_id(raw: str) -> str | None:
    match = _ID_RE.match(raw.strip().replace(" ", ""))
    return match.group(1) if match else None


def build_portal_teaser(db: Session, platform_code: str, raw_athlete_id: str) -> dict[str, object] | None:
    """Агрегаты участника для тизера; None — не нашли или нет финишей."""
    if platform_code not in TEASER_PLATFORMS:
        return None
    athlete_id = normalize_athlete_id(raw_athlete_id)
    if athlete_id is None:
        return None

    participant = (
        db.query(Participant)
        .join(Platform, Platform.id == Participant.platform_id)
        .filter(Platform.code == platform_code, Participant.external_user_id == athlete_id)
        .one_or_none()
    )
    if participant is None:
        return None

    finishes, best_sec, locations, first_date, last_date = (
        db.query(
            func.count(RunResult.id),
            func.min(RunResult.finish_time_sec),
            func.count(func.distinct(Event.location_id)),
            func.min(Event.event_date),
            func.max(Event.event_date),
        )
        .join(Event, Event.id == RunResult.event_id)
        .filter(
            RunResult.participant_id == participant.id,
            RunResult.finish_time_sec.isnot(None),
        )
        .one()
    )
    if not finishes:
        return None

    return {
        "platform_code": platform_code,
        "display_name": participant.display_name,
        "finishes": int(finishes),
        "best_time_display": format_finish_time(int(best_sec)) if best_sec else None,
        "locations": int(locations or 0),
        "first_event_date": first_date,
        "last_event_date": last_date,
    }
