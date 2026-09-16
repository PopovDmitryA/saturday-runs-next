#!/usr/bin/env python3
"""Перевесить забеги со «штрихкодных» личностей RunPark на их аккаунты.

Человек бежит по штрихкоду 5 вёрст — в выгрузке RunPark у строки пустой
participant_id, и мы заводим личность «barcode:A…». Позже он регистрируется, и
RunPark дописывает аккаунт СТАРЫМ результатам задним числом. Но `updated_at` у
них остаётся прежним, а инкрементальный синк ходит именно по нему — сам он эти
строки не заберёт никогда.

Свежие случаи с 14.09.2026 закрывает сам синк (reconcile_barcode_identity
вызывается, когда в протокол приходит строка с аккаунтом и штрихкодом). Этот
скрипт разбирает то, что накопилось раньше: на 14.09.2026 — 199 строк у 13
человек, ни у одного штрихкода источник не называет двух разных аккаунтов.

Ничего не склеиваем своей головой: для каждой штрихкодной личности спрашиваем
api.vw_run_results и перевешиваем ровно те строки, которые RunPark сам отдаёт с
аккаунтом. Привязка человека на сайте переезжает вместе с забегами, опустевшая
личность удаляется.

Идемпотентен: перевешивать нечего — молча ничего не делает.

Запуск:
    docker compose exec api python scripts/backfill_runpark_barcode_identities.py --dry-run
    make prod-run ARGS="scripts/backfill_runpark_barcode_identities.py"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import Participant, Platform, RunResult
from app.sync.runpark_global_sync import PLATFORM_CODE, reconcile_barcode_identity
from app.sync import upsert


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Откатить в конце, ничего не менять.")
    parser.add_argument("--limit", type=int, default=0, help="Взять только N личностей (для пробы).")
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        platform = upsert.get_platform(db, PLATFORM_CODE)
        stale = (
            db.query(Participant)
            .filter(
                Participant.platform_id == platform.id,
                Participant.external_user_id.like("barcode:%"),
                Participant.barcode_id.isnot(None),
            )
            .all()
        )
        # Только те, на ком реально висят забеги: пустышки трогать незачем.
        stale = [
            row
            for row in stale
            if db.query(RunResult).filter(RunResult.participant_id == row.id).count() > 0
        ]
        if args.limit:
            stale = stale[: args.limit]
        print(f"штрихкодных личностей с забегами: {len(stale)}")

        moved_total = 0
        touched = 0
        for index, row in enumerate(stale, 1):
            moved = reconcile_barcode_identity(db, platform, row.barcode_id)
            if moved:
                touched += 1
                moved_total += moved
                print(f"  {row.barcode_id:14} {str(row.display_name)[:26]:28} перевешено {moved}")
            if index % 500 == 0:
                print(f"  … просмотрено {index} из {len(stale)}")

        if args.dry_run:
            db.rollback()
            print(f"\nDRY-RUN: перевесил бы {moved_total} забегов у {touched} человек. Откатил.")
            print("Запустите без --dry-run, чтобы применить.")
        else:
            db.commit()
            print(f"\nПеревешено {moved_total} забегов у {touched} человек.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
