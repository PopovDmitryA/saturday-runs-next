#!/usr/bin/env python3
"""Проставить пол участникам RunPark по колонке vw_run_results.gender.

17.09.2026 RunPark по нашей просьбе добавил во вьюху забегов колонку `gender`
(«M» / «W»). До неё пол выводился из второй буквы возрастной категории, а её у
трети финишей нет вовсе: пол был неизвестен у 44 528 из 75 750 строк (59%).

Сам синк колонку уже читает, но старые протоколы он не перечитает: ходит он по
`updated_at`, а тот у прошлых строк не менялся. Этот скрипт разбирает
накопленное — одним проходом по вьюхе собирает пол по ключу личности и
раскладывает в participants.gender, после чего пересчитывает места по полу на
затронутых событиях.

Своей головой ничего не выдумываем: берём только то, что назвал источник.
Известный пол не перетираем молча — расхождения считаем и показываем отдельно,
менять их можно только с `--overwrite`.

Идемпотентен: второй прогон не находит работы.

Запуск:
    docker compose exec api python scripts/backfill_runpark_gender.py
    docker compose exec api python scripts/backfill_runpark_gender.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Event, Participant, RunResult
from app.runpark.mssql_client import runpark_query
from app.services.gender_position_service import (
    normalize_source_gender,
    recalculate_event_gender_positions,
)
from app.sync import upsert
from app.sync.runpark_global_sync import PLATFORM_CODE


def source_genders() -> tuple[dict[str, str], int]:
    """Пол по ключу личности, как его отдаёт RunPark. Плюс число разнобоев.

    Ключ строим ровно так же, как синк (_external_user_id): аккаунт — GUID,
    скан штрихкода — «barcode:A…», безымянный финиш — «anon:<result_id>».
    """
    rows = runpark_query(
        "SELECT result_id, participant_id, barcode_id, gender FROM api.vw_run_results "
        "WHERE gender IS NOT NULL"
    )
    seen: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        gender = normalize_source_gender(row.get("gender"))
        if gender is None:
            continue
        if row.get("participant_id"):
            key = str(row["participant_id"]).upper()
        elif row.get("barcode_id"):
            key = f"barcode:{row['barcode_id']}"
        else:
            key = f"anon:{str(row['result_id']).upper()}"
        seen[key].add(gender)
    # Одна личность с двумя полами — данные источника, а не наша беда: такие
    # пропускаем целиком, чтобы не выбирать наугад.
    conflicts = sum(1 for values in seen.values() if len(values) > 1)
    return {key: next(iter(values)) for key, values in seen.items() if len(values) == 1}, conflicts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Записать изменения (по умолчанию — только показать).")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Менять и тот пол, который у нас уже стоит иначе, чем у источника.",
    )
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform = upsert.get_platform(db, PLATFORM_CODE)
        by_key, conflicts = source_genders()
        print(f"Источник назвал пол для {len(by_key)} личностей; разнобоев — {conflicts}")

        participants = (
            db.query(Participant)
            .filter(Participant.platform_id == platform.id)
            .all()
        )
        filled = 0
        disagreed = 0
        changed_ids: list = []
        for participant in participants:
            gender = by_key.get(participant.external_user_id)
            if gender is None or participant.gender == gender:
                continue
            if participant.gender is not None:
                disagreed += 1
                if not args.overwrite:
                    continue
            else:
                filled += 1
            participant.gender = gender
            changed_ids.append(participant.id)

        print(f"Пол проставлен впервые: {filled}")
        print(f"Расходится с нашим: {disagreed}" + ("" if args.overwrite else " (не трогаю, нужен --overwrite)"))

        if not changed_ids:
            print("Менять нечего.")
            return 0

        event_ids = [
            row.event_id
            for row in db.query(RunResult.event_id)
            .filter(RunResult.participant_id.in_(changed_ids))
            .distinct()
            .all()
        ]
        print(f"Затронуто событий: {len(event_ids)}")

        if not args.apply:
            print("Пробный прогон, ничего не записано. Повторить с --apply.")
            db.rollback()
            return 0

        db.flush()
        for index, event_id in enumerate(event_ids, 1):
            recalculate_event_gender_positions(db, event_id, PLATFORM_CODE)
            if index % 200 == 0:
                db.commit()
                print(f"  пересчитано событий: {index}/{len(event_ids)}")
        db.commit()

        rows_with_gender = (
            db.query(RunResult)
            .join(Participant, Participant.id == RunResult.participant_id)
            .join(Event, Event.id == RunResult.event_id)
            .filter(Participant.platform_id == platform.id, Participant.gender.isnot(None))
            .count()
        )
        print(f"Готово. Строк протоколов с известным полом: {rows_with_gender}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
