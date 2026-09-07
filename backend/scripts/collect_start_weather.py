"""Сбор погоды на стартах из Open-Meteo в таблицу start_weather.

Периметр: локации России любой системы + зарубежные RunPark и S95, только с
координатами и хотя бы одним стартом. Даты — все субботы с первого старта плюс
внесубботние старты. Повторный запуск докачивает недостающие даты (--no-resume —
перезаписать всё). На суточном лимите Open-Meteo (10 000 условных вызовов, год
одной локации ≈ 26) скрипт останавливается со сводкой — запускать снова завтра.

Запуск:
  python scripts/collect_start_weather.py --location Якутск --location Мещерский
  python scripts/collect_start_weather.py --all --since 2022-01-01
  python scripts/collect_start_weather.py --all --dry-run      # только список локаций
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.services.weather_service import collect_scope, format_run_report, list_scope_locations  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Погода на стартах: сбор из Open-Meteo")
    parser.add_argument("--location", action="append", help="подстрока названия локации (можно несколько раз)")
    parser.add_argument("--all", action="store_true", help="все локации периметра")
    parser.add_argument("--since", type=date.fromisoformat, help="не раньше этой даты (YYYY-MM-DD)")
    parser.add_argument("--events-only", action="store_true", help="только даты стартов, без пустых суббот")
    parser.add_argument("--pause", type=float, default=0.3, help="пауза между вызовами API, сек")
    parser.add_argument("--no-resume", action="store_true", help="перезаписать и уже собранные даты")
    parser.add_argument("--dry-run", action="store_true", help="показать периметр и выйти")
    args = parser.parse_args()
    if not args.location and not args.all:
        parser.error("нужно --location или --all")

    db = get_session_factory()()
    locations = list_scope_locations(db, args.location)
    print(f"Локаций в периметре: {len(locations)}")
    if args.dry_run:
        for loc in locations:
            sched = "расписание" if loc.schedule else "09:00 по умолчанию"
            print(f"  {loc.platform_code:12} {loc.name} ({loc.country}) — {sched}")
        return

    def log_location(loc: object, stats: object) -> None:
        if not stats.api_calls:  # type: ignore[attr-defined]
            return
        print(
            f"  {loc.platform_code:12} {loc.name}: дат {stats.dates_requested}, уже было {stats.dates_already}, "  # type: ignore[attr-defined]
            f"записано {stats.rows_written}, без данных {stats.skipped_no_data}, вызовов {stats.api_calls}"  # type: ignore[attr-defined]
        )

    with httpx.Client(headers={"User-Agent": "run5k.run weather collector"}) as client:
        summary = collect_scope(
            db,
            client,
            name_filters=args.location,
            since=args.since,
            events_only=args.events_only,
            resume=not args.no_resume,
            pause_seconds=args.pause,
            on_location=log_location,
        )
    print(format_run_report(summary, when=datetime.now()))
    if summary.stopped_by_limit:
        sys.exit(3)


if __name__ == "__main__":
    main()
