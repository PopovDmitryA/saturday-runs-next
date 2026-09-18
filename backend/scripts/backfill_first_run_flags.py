#!/usr/bin/env python3
"""Пересчёт is_first_run / is_first_run_at_location для s95, parkrun, runpark.

five_verst (и старый HTML-путь s95) берут эти поля с иконок достижений самого
сайта — их не трогаем. Здесь платформы, где поле не приезжает ниоткуда и
выводится из хронологии: JSON API s95, parkrun, runpark.

Скрипт нужен не только разово: флаг лежит в run_results, а upsert протокола
переписывает его тем, что дал адаптер (то есть False). Любой путь синка,
забывший про пересчёт, тихо роняет «Новичков» — поэтому `--check` показывает
расхождение сохранённых флагов с правилом, ничего не записывая.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.personal_record_service import (
    FIRST_RUN_DERIVED_PLATFORMS,
    first_run_flag_mismatches,
    recalculate_first_run_flags,
    repair_first_run_flags,
)

SUPPORTED = tuple(sorted(FIRST_RUN_DERIVED_PLATFORMS))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive is_first_run / is_first_run_at_location from chronological run order"
    )
    parser.add_argument(
        "--platform",
        choices=[*SUPPORTED, "all"],
        default="all",
        help="Platform to recalculate (default: all)",
    )
    parser.add_argument(
        "--participant-id",
        help="Limit to one participant UUID",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Только проверка: сколько строк разошлись с правилом, ничего не пишем",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Пересчитать только тем участникам, у кого флаги разошлись (быстро)",
    )
    args = parser.parse_args()

    participant_id = UUID(args.participant_id) if args.participant_id else None
    platforms = list(SUPPORTED) if args.platform == "all" else [args.platform]

    db = get_session_factory()()
    try:
        if args.check:
            mismatched = 0
            for platform_code in platforms:
                stats = first_run_flag_mismatches(db, platform_code)
                mismatched += stats["first_run_mismatch"] + stats["first_at_location_mismatch"]
                print(
                    f"{platform_code}: is_first_run={stats['first_run_mismatch']} "
                    f"(лишних {stats['extra_first_run']}, потерянных {stats['missing_first_run']}), "
                    f"is_first_run_at_location={stats['first_at_location_mismatch']}",
                    flush=True,
                )
            return 1 if mismatched else 0

        if args.repair:
            for platform_code in platforms:
                stats = repair_first_run_flags(db, platform_code)
                db.commit()
                print(
                    f"{platform_code}: participants_repaired={stats['participants_repaired']} "
                    f"runs_updated={stats['runs_updated']}",
                    flush=True,
                )
            return 0

        for platform_code in platforms:
            stats = recalculate_first_run_flags(
                db,
                platform_code,
                participant_id=participant_id,
            )
            db.commit()
            print(
                f"{platform_code}: participants_touched={stats['participants_touched']} "
                f"runs_updated={stats['runs_updated']}",
                flush=True,
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
