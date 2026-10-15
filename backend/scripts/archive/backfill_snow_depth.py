#!/usr/bin/env python3
"""Разовая дозаливка снежного покрова в окончательную погоду стартов.

ERA5 покров не отдаёт, и после переписи истории на ERA5 (17.09.2026) у всех
окончательных строк start_weather snow_depth_cm пустой — клетка «По снегу» во
«Всепогодном» пропала за все зимы. Новые строки покров получают сами (ERA5-Land
вторым запросом, weather_service.fetch_final), а этот скрипт возвращает его в
историю: по локации — запрос одной переменной на год, ≈2.6 вызова Open-Meteo.
Вся история — ≈1 600 запросов и ≈3 800 вызовов при суточном лимите 10 000.

Пишет только пустой покров, остального в строках не трогает. После заливки
тихо двигает снимки «Всепогодного» у подписчиков уведомлений: вернувшиеся
клетки прошлых зим не придут сообщением как новость (решение Дмитрия
14.10.2026).

Темп — не больше --weight-per-hour вызовов в час: часовой лимит Open-Meteo
(5 000) общий с ночными сборами погоды на том же адресе. Прерывать безопасно,
повторный запуск доливает остаток; на суточном лимите или своём потолке —
выход с кодом 3.

    python scripts/archive/backfill_snow_depth.py --dry-run
    CONFIRM_PROD=1 python scripts/archive/backfill_snow_depth.py
    CONFIRM_PROD=1 python scripts/archive/backfill_snow_depth.py --location Якутск

Прогнан на проде 15.10.2026: пустой покров был у 66 006 строк era5, после
прогона — ни у одной; покров от 1 см у 25 618, снимки тихо сдвинуты у 76
подписчиков.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from sqlalchemy import or_, select  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.models import UserNotificationPrefs  # noqa: E402
from app.services.activity_notification_service import refresh_challenge_snapshots_quietly  # noqa: E402
from app.services.weather_service import (  # noqa: E402
    RateLimitDaily,
    SnowBackfillStats,
    backfill_snow_depth,
    snow_backfill_weight,
    snow_gap_dates,
    snow_gap_locations,
)
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args  # noqa: E402

# Покров читает только клетка «По снегу» во «Всепогодном» (achievements_service).
SNOW_CHALLENGES = {"all_weather"}


def refresh_snapshots(db) -> int:  # type: ignore[no-untyped-def]
    user_ids = db.execute(
        select(UserNotificationPrefs.user_id).where(
            or_(UserNotificationPrefs.challenge_levels.isnot(None), UserNotificationPrefs.challenge_counts.isnot(None))
        )
    ).scalars()
    refreshed = 0
    for user_id in list(user_ids):
        if refresh_challenge_snapshots_quietly(db, user_id, SNOW_CHALLENGES):
            refreshed += 1
        db.commit()
    return refreshed


def main() -> int:
    parser = argparse.ArgumentParser(description="Снежный покров ERA5-Land в окончательную погоду стартов")
    parser.add_argument("--location", action="append", help="подстрока названия локации (можно несколько раз)")
    parser.add_argument("--max-weight", type=int, default=4500, help="потолок вызовов Open-Meteo за прогон")
    parser.add_argument("--weight-per-hour", type=int, default=2500, help="темп, вызовов Open-Meteo в час")
    parser.add_argument("--skip-snapshots", action="store_true", help="не двигать снимки челленджей подписчиков")
    parser.add_argument("--dry-run", action="store_true", help="посчитать строки и вызовы, без сети и записи")
    add_bootstrap_args(parser)
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    locations = [
        loc
        for loc in snow_gap_locations(db)
        if not args.location or any(f.lower() in loc.name.lower() for f in args.location)
    ]
    print(f"Локаций с пустым покровом: {len(locations)}, строк: {sum(loc.rows for loc in locations)}", flush=True)
    if args.dry_run:
        weight = sum(snow_backfill_weight(snow_gap_dates(db, loc.id)) for loc in locations)
        minutes = weight * 60 // max(args.weight_per_hour, 1)
        print(f"Вызовов Open-Meteo ≈{weight} (суточный лимит 10 000); при {args.weight_per_hour}/ч — ≈{minutes} мин")
        return 0

    totals = SnowBackfillStats()
    exit_code = 0
    pause_per_weight = 3600 / args.weight_per_hour
    with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}) as client:
        for loc in locations:
            budget = args.max_weight - totals.weighted_calls
            try:
                stats = backfill_snow_depth(db, client, loc, weight_budget=budget, pause_per_weight=pause_per_weight)
            except RateLimitDaily as exc:
                db.rollback()
                print(f"Суточный лимит Open-Meteo: {exc}. Остаток — повторным запуском завтра", flush=True)
                exit_code = 3
                break
            for field in ("rows_missing", "rows_filled", "rows_with_cover", "rows_no_data", "api_calls"):
                setattr(totals, field, getattr(totals, field) + getattr(stats, field))
            totals.weighted_calls += stats.weighted_calls
            print(
                f"  {loc.name}: пустых {stats.rows_missing}, залито {stats.rows_filled}, "
                f"с покровом {stats.rows_with_cover}, без данных {stats.rows_no_data}, вызовов {stats.weighted_calls}",
                flush=True,
            )
            if stats.stopped_by_budget:
                print(f"Потолок --max-weight {args.max_weight} — остаток повторным запуском", flush=True)
                exit_code = 3
                break
    print(
        f"Итого: залито {totals.rows_filled} строк, из них с покровом от 1 см {totals.rows_with_cover}, "
        f"без данных {totals.rows_no_data}; запросов {totals.api_calls}, вызовов Open-Meteo {totals.weighted_calls}",
        flush=True,
    )
    if totals.rows_filled and not args.skip_snapshots:
        print(f"Тихо обновлены снимки «Всепогодного»: {refresh_snapshots(db)} подписчиков", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
