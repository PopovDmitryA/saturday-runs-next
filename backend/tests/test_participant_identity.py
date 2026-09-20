"""Заглушка вместо имени — не человек, и уникальных участников она не прибавляет.

Репорт 20.09.2026 из Шадринска: карточка локации показывала 1104 участника
против 922 у 5 вёрст. Разницу давали 183 строки «НЕИЗВЕСТНЫЙ» — под каждую
заводится одноразовая личность, и счётчик считал её отдельным человеком.
Правило одно на весь сайт: финиши считаем со всеми, уникальных — только
опознанных.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import func

from app.participant_identity import identified_participant_clause, is_anonymous_participant


def test_is_anonymous_participant_by_key_and_name() -> None:
    assert is_anonymous_participant("unknown:keldyusheva:2026-08-01:2", "НЕИЗВЕСТНЫЙ") is True
    assert is_anonymous_participant("anon:12345", "Неизвестный бегун") is True
    assert is_anonymous_participant(None, "Кто-то") is True
    assert is_anonymous_participant("790139801", "Живой БЕГУН") is False


def test_is_anonymous_participant_keeps_real_surname() -> None:
    """«Андрей НЕИЗВЕСТНЫХ» — настоящая фамилия, а не заглушка."""
    assert is_anonymous_participant("790139801", "Андрей НЕИЗВЕСТНЫХ") is False


def test_identified_participant_clause_filters_placeholders(db_session: Any) -> None:
    from app.models import Participant, Platform

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()

    suffix = str(uuid4().int % 1_000_000)
    people = [
        Participant(platform_id=platform.id, external_user_id=f"{suffix}-real", display_name="Живой БЕГУН"),
        Participant(
            platform_id=platform.id,
            external_user_id=f"unknown:loc-{suffix}:2026-08-01:2",
            display_name="НЕИЗВЕСТНЫЙ",
        ),
        Participant(platform_id=platform.id, external_user_id=f"anon:{suffix}", display_name="Неизвестный бегун"),
        # Штрихкод есть, имени нет — 5 вёрст печатают такие строки как «Unknown #67».
        Participant(platform_id=platform.id, external_user_id=f"{suffix}-nameless", display_name="Unknown #67"),
    ]
    db_session.add_all(people)
    db_session.flush()
    ids = [person.id for person in people]

    named = (
        db_session.query(func.count(Participant.id))
        .filter(
            Participant.id.in_(ids),
            identified_participant_clause(Participant.external_user_id, Participant.display_name),
        )
        .scalar()
    )

    assert named == 1
