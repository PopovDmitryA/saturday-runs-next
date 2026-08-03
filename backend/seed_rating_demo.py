#!/usr/bin/env python3
"""Добавить в локальный рейтинг дальности участников с разными пометками дома.

В репозиторий не коммитится — сид для стенда worktree. Заводит трёх бегунов:
- чёткий дом (пометки нет);
- неоднозначный автовыбор (янтарный «!»);
- ручной выбор дома вне топ-3 (красный «!»), для него создаётся и пользователь
  сайта, потому что home_location_key живёт в users.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path("/app")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.db.session import get_session_factory

# Площадки берём те же, что у первого демо-бегуна (их уже завёл прошлый сид),
# плюс пара дальних — чтобы новые строки попали в верх рейтинга.
PLACES = {
    "demo-bitsa": ("Битца", "Москва", 55.5900, 37.5600),
    "demo-meshchersky": ("Мещерский", "Москва", 55.6600, 37.4000),
    "demo-khabarovsk": ("Хабаровск Динамо", "Хабаровск", 48.4800, 135.0700),
    "demo-sochi": ("Сириус", "Сочи", 43.4100, 39.9500),
    "demo-kazan": ("Казань Горкинско-Ометьевский", "Казань", 55.7700, 49.1800),
    "demo-vladivostok": ("Владивосток Морской", "Владивосток", 43.1150, 131.8850),
    "demo-kaliningrad": ("Калининград Южный", "Калининград", 54.6900, 20.5000),
}

RUNNERS = [
    {
        "key": "demo-clear",
        "name": "Демо · чёткий дом",
        # Битца заметно впереди — пометки быть не должно.
        "runs": {"demo-bitsa": 14, "demo-khabarovsk": 1, "demo-vladivostok": 1},
        "manual_home": None,
    },
    {
        "key": "demo-ambiguous",
        "name": "Демо · спорный автодом",
        # 8 против 7 — разрыв меньше 30%, автовыбор неоднозначен.
        "runs": {
            "demo-bitsa": 8,
            "demo-meshchersky": 7,
            "demo-khabarovsk": 1,
            "demo-vladivostok": 1,
        },
        "manual_home": None,
    },
    {
        "key": "demo-manual",
        "name": "Демо · дом выбран вручную",
        "runs": {
            "demo-bitsa": 12,
            "demo-meshchersky": 8,
            "demo-sochi": 6,
            "demo-kazan": 4,
            "demo-kaliningrad": 2,
            "demo-khabarovsk": 1,
        },
        # Пятая по числу визитов площадка — вне топ-3.
        "manual_home": "demo-kaliningrad",
    },
]


def main() -> int:
    db = get_session_factory()()
    platform = db.query(Platform).filter(Platform.code == "five_verst").one()

    locations: dict[str, Location] = {}
    for slug, (name, city, latitude, longitude) in PLACES.items():
        location = (
            db.query(Location)
            .filter(Location.platform_id == platform.id, Location.external_key == slug)
            .one_or_none()
        )
        if location is None:
            location = Location(
                platform_id=platform.id,
                external_key=slug,
                name=name,
                city=city,
                country="Россия",
                latitude=latitude,
                longitude=longitude,
                is_official_map=True,
                source_url=f"https://5verst.ru/{slug}/",
            )
            db.add(location)
            db.flush()
        locations[slug] = location

    saturday = date(2026, 8, 1)
    for runner in RUNNERS:
        key = str(runner["key"])
        participant = (
            db.query(Participant)
            .filter(Participant.platform_id == platform.id, Participant.external_user_id == key)
            .one_or_none()
        )
        if participant is None:
            participant = Participant(
                platform_id=platform.id,
                external_user_id=key,
                display_name=str(runner["name"]),
                profile_url=f"https://example.test/{key}/",
                gender="male",
            )
            db.add(participant)
            db.flush()

        # Даты площадок разносим: иначе каждая площадка получала бы субботу
        # 01.08, и «за последнюю неделю» у бегуна выходило бы пять стартов в
        # один день — на реальных данных так не бывает.
        for offset, (slug, count) in enumerate(dict(runner["runs"]).items()):  # type: ignore[arg-type]
            for index in range(int(count)):
                event_date = saturday - timedelta(days=7 * (offset + index))
                # Старт на площадке один на всех — событие переиспользуем
                # (уникальность по платформе, локации и дате), добавляем в него
                # только свой результат.
                event = (
                    db.query(Event)
                    .filter(
                        Event.platform_id == platform.id,
                        Event.location_id == locations[slug].id,
                        Event.event_date == event_date,
                    )
                    .one_or_none()
                )
                if event is None:
                    event = Event(
                        platform_id=platform.id,
                        location_id=locations[slug].id,
                        external_event_key=f"demo:{slug}:{event_date.isoformat()}",
                        event_date=event_date,
                        event_number=index + 1,
                        title=f"{PLACES[slug][0]} #{index + 1}",
                    )
                    db.add(event)
                    db.flush()
                result_key = f"{key}:{slug}:{event_date.isoformat()}"
                if (
                    db.query(RunResult)
                    .filter(RunResult.external_result_key == result_key)
                    .one_or_none()
                    is not None
                ):
                    continue
                db.add(
                    RunResult(
                        event_id=event.id,
                        participant_id=participant.id,
                        external_result_key=result_key,
                        position=5,
                        finish_time_sec=26 * 60 + index,
                        finish_time_display="00:26:00",
                        status="finished",
                    )
                )

        manual_home = runner["manual_home"]
        if manual_home is None:
            continue
        telegram_id = 999000000 + abs(hash(key)) % 100000
        user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                telegram_username=key.replace("-", "_"),
                display_name=str(runner["name"]),
            )
            db.add(user)
            db.flush()
        # Связка уникальна и по участнику тоже — проверяем оба конца, иначе
        # повторный прогон сида падает на уникальном индексе.
        if (
            db.query(PlatformLink)
            .filter(
                (PlatformLink.participant_id == participant.id)
                | (
                    (PlatformLink.user_id == user.id)
                    & (PlatformLink.platform_id == platform.id)
                )
            )
            .first()
            is None
        ):
            db.add(
                PlatformLink(
                    user_id=user.id,
                    platform_id=platform.id,
                    participant_id=participant.id,
                    external_user_id=key,
                    external_url=participant.profile_url,
                )
            )
        # Ключ идентичности некаталожной локации — «location:<uuid>».
        user.home_location_key = f"location:{locations[str(manual_home)].id}"
        print(f"{runner['name']}: дом вручную -> {user.home_location_key}")

    db.commit()
    db.close()
    print("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
