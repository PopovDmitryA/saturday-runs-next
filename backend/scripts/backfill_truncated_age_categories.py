"""Бэкфилл обрезанных возрастных категорий 5 вёрст («М11» ← «М110-114»).

Старая AGE_CATEGORY_RE брала букву и ровно две цифры, поэтому у участников с
незаполненной датой рождения (5 вёрст считает им абсурдный возраст и печатает
трёхзначную границу) в базу уезжала несуществующая категория: «М110-114» →
«М11». Парсер починен, но накопленные строки надо перечитать: восстановить
исходное значение из самой базы нельзя — «М12» это и «М120-124», и «М125-129».

Скрипт перечитывает протоколы затронутых забегов и переписывает
run_results.age_category (и participants.age_category, куда уехала та же
обрезка). По умолчанию — сухой прогон без единой записи.

    python scripts/backfill_truncated_age_categories.py            # только показать
    python scripts/backfill_truncated_age_categories.py --limit 5  # первые 5 протоколов
    python scripts/backfill_truncated_age_categories.py --apply    # записать

DATABASE_URL берётся из окружения: для прода — через scripts/prod_db_env.sh.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.models import Event, Location, Participant, Platform, RunResult
from app.platform_adapters.five_verst.bulk_parser import fetch_run_protocol

# Обрезки, которые мог оставить старый парсер: буква пола и ровно две цифры без
# диапазона. Легальная детская категория «М10»/«Ж10» под это описание тоже
# подходит, поэтому исключаем её явно.
TRUNCATED_PATTERN = r"^[МЖ][0-9]{2}$"
LEGAL_SINGLE_DIGIT_GROUPS = ("М10", "Ж10")


@dataclass
class Affected:
    result_id: object
    participant_id: object
    external_user_id: str
    slug: str
    event_date: object
    event_number: int | None
    stored: str


def find_affected(db: Session) -> list[Affected]:
    rows = db.execute(
        select(
            RunResult.id,
            RunResult.participant_id,
            Participant.external_user_id,
            Location.external_key,
            Event.event_date,
            Event.event_number,
            RunResult.age_category,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .where(
            Platform.code == "five_verst",
            RunResult.age_category.op("~")(TRUNCATED_PATTERN),
            RunResult.age_category.notin_(LEGAL_SINGLE_DIGIT_GROUPS),
        )
        .order_by(Event.event_date)
    ).all()
    return [Affected(*row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать изменения (по умолчанию сухой прогон)")
    parser.add_argument("--limit", type=int, default=None, help="обработать только первые N протоколов")
    args = parser.parse_args()

    engine = get_engine()

    # Коннект к БД открываем тремя короткими сессиями и НЕ держим его во время
    # скачивания протоколов: сотня медленных HTTP-запросов внутри транзакции —
    # это idle-in-transaction, за который прод рвёт соединение (и из-за чего
    # однажды выело пул).
    with Session(engine) as db:
        affected = find_affected(db)
    if args.limit is not None:
        affected = affected[: args.limit]
    print(f"строк к перечитыванию: {len(affected)}")

    updates: list[tuple[Affected, str | None]] = []
    unchanged = 0
    missing = 0
    for item in affected:
        try:
            results, _html = fetch_run_protocol(item.slug, item.event_date, item.event_number)
        except Exception as exc:  # noqa: BLE001 — протокол мог переехать, идём дальше
            print(f"  ! {item.slug} {item.event_date}: не забрать ({type(exc).__name__}: {exc})")
            missing += 1
            continue

        parsed = next((r for r in results if r.external_user_id == item.external_user_id), None)
        if parsed is None:
            print(f"  ! {item.slug} {item.event_date}: участника {item.external_user_id} нет в протоколе")
            missing += 1
            continue

        if parsed.age_category == item.stored:
            unchanged += 1
            continue

        print(f"  {item.slug} {item.event_date} {item.external_user_id}: {item.stored} → {parsed.age_category}")
        updates.append((item, parsed.age_category))

    if not args.apply:
        print(f"\nбудет исправлено: {len(updates)} (сухой прогон, ничего не записано)")
        print(f"уже верных: {unchanged}, не удалось перечитать: {missing}")
        return 0

    with Session(engine) as db:
        for item, actual in updates:
            db.query(RunResult).filter(RunResult.id == item.result_id).update(
                {"age_category": actual}, synchronize_session=False
            )
            # В профиль участника уехала та же обрезка — правим, если она там ещё лежит.
            db.query(Participant).filter(
                Participant.id == item.participant_id,
                Participant.age_category == item.stored,
            ).update({"age_category": actual}, synchronize_session=False)
        db.commit()
    print(f"\nзаписано: {len(updates)}")
    print(f"уже верных: {unchanged}, не удалось перечитать: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
