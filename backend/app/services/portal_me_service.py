"""Личная плашка на главной для залогиненного (гипотеза Т4).

Зачем: главная для своих — турникет. За 30 дней 185 залогиненных открыли её
1010 раз, и в 42.6% случаев следующим шагом уходили в кабинет. То есть человек
читает приглашение «найдите здесь себя» уже после того, как себя нашёл, и
кликает ещё раз. Плашка отвечает на вопрос, ради которого он и пришёл: как
прошла его последняя суббота.

Отдельная лёгкая ручка, а не поле в /portal/home: главная кэшируется и одна на
всех, а это персональный ответ. И грузится она после главной, поэтому анонимный
путь — самый массовый — от неё не зависит совсем.

Чего здесь намеренно НЕТ: суммарных «столько-то пробежек» и «столько-то
локаций». В кабинете они считаются с дедупликацией кросслинков и через каталог
локаций (compute_dashboard_stats, count_user_unique_locations) — повторить это
дёшево нельзя, а свой упрощённый счёт разъехался бы с кабинетом на глазах у
человека. Серия суббот такой беды лишена: она считается по МНОЖЕСТВУ дат, и
дубли кросслинков в нём схлопываются сами.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.dashboard_service import _current_saturday_streak
from app.services.portal_home_service import format_finish_time


def _participant_join() -> Any:
    """Надёжный ключ participants ↔ platform_links — (platform_id,
    external_user_id), а не platform_links.participant_id: он проставлен не
    всегда (см. _dashboard_platform_link_join)."""
    return and_(
        PlatformLink.platform_id == Participant.platform_id,
        PlatformLink.external_user_id == Participant.external_user_id,
    )


def _finished_runs_query(db: Session, user_id: UUID) -> Any:
    """Состоявшиеся финиши пользователя по всем его платформам.

    Строки без finish_time_sec — отметки об участии без результата; в личных
    цифрах они дали бы завышенный счёт.
    """
    return (
        db.query(RunResult, Event)
        .join(Event, Event.id == RunResult.event_id)
        .join(Participant, Participant.id == RunResult.participant_id)
        .join(PlatformLink, _participant_join())
        .filter(
            PlatformLink.user_id == user_id,
            RunResult.finish_time_sec.isnot(None),
        )
    )


def build_portal_me(db: Session, user: User, *, today: date | None = None) -> dict[str, object]:
    """Короткая личная сводка: последняя пробежка и живая серия суббот.

    linked=False — привязанных профилей нет. Для главной это не пустой ответ, а
    повод показать призыв привязать профиль: именно эти люди (195 из 674
    регистраций за 120 дней) до своей статистики так и не дошли.
    """
    has_link = db.query(PlatformLink.id).filter(PlatformLink.user_id == user.id).first() is not None
    if not has_link:
        return {"linked": False, "last_run": None, "saturday_streak": 0}

    last = (
        _finished_runs_query(db, user.id)
        .join(Location, Location.id == Event.location_id)
        .join(Platform, Platform.id == Event.platform_id)
        .with_entities(
            Event.event_date,
            Location.name.label("location_name"),
            Platform.code.label("platform_code"),
            RunResult.finish_time_sec,
            RunResult.is_pr,
            RunResult.is_global_pr,
        )
        # Один и тот же старт может прийти с двух платформ (кросслинк) — из
        # пары берём тот же результат, что покажет кабинет: быстрейший.
        .order_by(Event.event_date.desc(), RunResult.finish_time_sec.asc())
        .first()
    )
    if last is None:
        # Профиль привязан, но финишей нет: свежая привязка, синк ещё идёт.
        return {"linked": True, "last_run": None, "saturday_streak": 0}

    # Серия считается по датам стартов, поэтому нужны все даты, а не последняя.
    # Строк столько же, сколько пробежек: у самых активных — сотни, для одного
    # пользователя это дёшево.
    activity_dates = {
        row[0] for row in _finished_runs_query(db, user.id).with_entities(Event.event_date).all()
    }

    return {
        "linked": True,
        "last_run": {
            "event_date": last.event_date,
            "location_name": last.location_name,
            "platform_code": last.platform_code,
            "finish_time_display": format_finish_time(int(last.finish_time_sec)),
            "is_pr": bool(last.is_pr),
            "is_global_pr": bool(last.is_global_pr),
        },
        "saturday_streak": _current_saturday_streak(activity_dates, today or date.today()),
    }
