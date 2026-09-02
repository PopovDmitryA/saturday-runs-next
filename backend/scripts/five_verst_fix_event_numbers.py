#!/usr/bin/env python3
"""Сверить и починить номера забегов 5 вёрст по странице локации.

Источник истины — `/{slug}/results/all/`: там номер стоит рядом с датой. В базу
же номер мог попасть из волонтёрской таблицы профиля, а она у 5 вёрст показывает
другой номер (у Дружбы за 15.08.2026 — «#226» вместо #228), и журнал протоколов
шёл вразнобой. Сам источник ошибки закрыт в upsert.py; этот скрипт разбирает то,
что уже записано.

По умолчанию проходит только по площадкам с ломаной нумерацией — там, где номер
не растёт вместе с датой. `--all` берёт все, `--slug` — конкретные.

    make prod-run ARGS="scripts/five_verst_fix_event_numbers.py --dry-run --pretty"
    CONFIRM_PROD=1 make prod-run ARGS="scripts/five_verst_fix_event_numbers.py --pretty"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.session import get_session_factory
from app.models import Event, Location, Platform
from app.platform_adapters.five_verst.bulk_parser import fetch_event_summaries
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env

PLATFORM_CODE = "five_verst"

# Площадки, где номер не растёт вместе с датой: верный признак, что кто-то
# записал чужой номер. Остальные трогать незачем — это лишние запросы к 5 вёрст.
BROKEN_LOCATIONS_SQL = text(
    """
    WITH seq AS (
        SELECT l.external_key,
               e.event_number,
               LAG(e.event_number) OVER (PARTITION BY e.location_id ORDER BY e.event_date) AS prev_num
        FROM events e
        JOIN locations l ON l.id = e.location_id
        JOIN platforms p ON p.id = l.platform_id
        WHERE p.code = :platform_code
          AND e.event_number IS NOT NULL
          AND e.is_test_event IS FALSE
    )
    SELECT DISTINCT external_key
    FROM seq
    WHERE prev_num IS NOT NULL AND event_number <= prev_num
    ORDER BY external_key
    """
)


def _target_slugs(db, args: argparse.Namespace) -> list[str]:
    if args.slug:
        return list(args.slug)
    if args.all:
        return [
            key
            for (key,) in db.query(Location.external_key)
            .join(Platform, Platform.id == Location.platform_id)
            .filter(Platform.code == PLATFORM_CODE)
            .order_by(Location.external_key)
            .all()
        ]
    return [row[0] for row in db.execute(BROKEN_LOCATIONS_SQL, {"platform_code": PLATFORM_CODE})]


def _fix_location(db, platform: Platform, slug: str, *, dry_run: bool) -> dict[str, object]:
    location = (
        db.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key == slug)
        .one_or_none()
    )
    if location is None:
        return {"slug": slug, "error": "локации нет в базе"}

    location_id, location_name = location.id, location.name
    # Ходить в сеть с открытой транзакцией нельзя: 5 вёрст отдают страницу через
    # общий координатор, тот умеет ждать пользовательский синк минутами, а прод
    # рвёт сессию по idle_in_transaction_session_timeout.
    db.commit()

    summaries, _html = fetch_event_summaries(slug, location_name)
    true_numbers = {s.event_date: s.event_number for s in summaries if s.event_number}
    if not true_numbers:
        return {"slug": slug, "error": "страница локации не отдала ни одного номера"}

    rows = (
        db.query(Event)
        .filter(Event.location_id == location_id, Event.is_test_event.is_(False))
        .order_by(Event.event_date)
        .all()
    )
    mismatches: list[dict[str, object]] = []
    for row in rows:
        expected = true_numbers.get(row.event_date)
        if expected is None or expected == row.event_number:
            continue
        mismatches.append(
            {
                "event_date": row.event_date.isoformat(),
                "было": row.event_number,
                "стало": expected,
            }
        )
        if not dry_run:
            row.event_number = expected
            if row.title and "#" in row.title:
                row.title = f"{row.title.split('#')[0].strip()} #{expected}"

    if mismatches and not dry_run:
        db.commit()
    else:
        db.rollback()

    return {
        "slug": slug,
        "events_checked": len(rows),
        "mismatches": len(mismatches),
        "items": mismatches,
    }


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Fix 5 вёрст event numbers from the location page")
    add_bootstrap_args(parser)
    parser.add_argument("--slug", action="append", help="Конкретная площадка (можно повторять)")
    parser.add_argument("--all", action="store_true", help="Все площадки 5 вёрст, а не только ломаные")
    parser.add_argument("--dry-run", action="store_true", help="Только показать расхождения")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == PLATFORM_CODE).one()
        slugs = _target_slugs(db, args)
        results = []
        for slug in slugs:
            try:
                results.append(_fix_location(db, platform, slug, dry_run=args.dry_run))
            except Exception as exc:
                db.rollback()
                results.append({"slug": slug, "error": str(exc)})
    finally:
        db.close()

    payload = {
        "dry_run": args.dry_run,
        "locations": len(results),
        "mismatches_total": sum(int(r.get("mismatches") or 0) for r in results),
        "errors": [r for r in results if r.get("error")],
        "items": [r for r in results if r.get("mismatches")],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
