#!/usr/bin/env python3
"""Перезалить протоколы 5 вёрст, в которых не хватает строк.

Зачем. Парсер выбрасывал финишёров без ссылки на профиль, если они подписаны не
«НЕИЗВЕСТНЫЙ», а «Имя Фамилия (Нужна регистрация)». Такие строки есть в каждом
десятом протоколе: у Дружбы 29.11.2025 потерялись позиции 67 и 118, и рекорд
посещаемости показывался как 193 вместо 195. Парсер починен, но уже сохранённые
протоколы сами не перечитаются — этим занимается скрипт.

Как ищем дырявые. Основной признак — разрыв в позициях: максимальная позиция
больше числа сохранённых строк. Он не зависит от того, заполнено ли
events.finishers_count, и ловит ровно то, что потеряно. Дополнительно берём
события, где строк меньше заявленного платформой числа финишёров.

Скрипт ходит в сеть по одному событию и уважает лимиты 5 вёрст (пауза между
запросами). Прогоняется частями: --limit ограничивает пачку, повторный запуск
берёт следующие.

По умолчанию только показывает, что будет перезалито. Запись — с --apply.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.session import get_session_factory
from app.models import EventSummary, Location, Platform, RunResult
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
from app.sync.five_verst_reconcile import _summary_to_canonical

FIVE_VERST = "five_verst"

# Событие считаем дырявым, если в позициях есть разрыв ИЛИ строк меньше, чем
# платформа заявила финишёров. Оба признака независимы: finishers_count
# заполнен не у всех событий, а разрыв виден всегда.
INCOMPLETE_EVENTS_SQL = """
WITH per_event AS (
    SELECT e.id,
           e.event_date,
           e.finishers_count,
           l.external_key AS slug,
           l.name AS location_name,
           count(rr.id) AS stored,
           max(rr.position) AS max_position
    FROM events e
    JOIN platforms p ON p.id = e.platform_id
    JOIN locations l ON l.id = e.location_id
    LEFT JOIN run_results rr ON rr.event_id = e.id
    WHERE p.code = :platform
      AND NOT e.is_test_event
      AND e.event_date > DATE '1970-01-01'
    GROUP BY e.id, e.event_date, e.finishers_count, l.external_key, l.name
)
SELECT id, event_date, slug, location_name, stored, max_position, finishers_count,
       greatest(
           coalesce(max_position, 0) - stored,
           coalesce(finishers_count, 0) - stored
       ) AS missing
FROM per_event
WHERE stored > 0
  AND (
        coalesce(max_position, 0) > stored
     OR (finishers_count IS NOT NULL AND finishers_count > stored)
  )
ORDER BY missing DESC, event_date DESC
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Перезалить протоколы (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=50, help="Сколько событий обработать за прогон (0 — все)")
    parser.add_argument("--delay", type=float, default=1.5, help="Пауза между запросами к 5 вёрст, сек")
    parser.add_argument("--slug", help="Ограничить одной локацией (для проверки)")
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as db:
        rows = db.execute(text(INCOMPLETE_EVENTS_SQL), {"platform": FIVE_VERST}).fetchall()
        if args.slug:
            rows = [row for row in rows if row.slug == args.slug]

        total_missing = sum(int(row.missing) for row in rows)
        print(f"дырявых протоколов: {len(rows)}, не хватает строк всего: {total_missing}")
        if not rows:
            return 0

        batch = rows if args.limit <= 0 else rows[: args.limit]
        print(f"в этом прогоне: {len(batch)}\n")

        if not args.apply:
            for row in batch:
                print(
                    f"  {row.location_name[:26]:<27} {row.event_date} "
                    f"строк={row.stored:<5} макс_позиция={row.max_position or 0:<5} "
                    f"заявлено={row.finishers_count or '—':<5} не хватает={row.missing}"
                )
            print("\nпробный прогон, ничего не перезаливалось — добавь --apply")
            return 0

        platform = db.query(Platform).filter(Platform.code == FIVE_VERST).one()
        repaired = failed = unchanged = 0
        recovered_rows = 0

        for index, row in enumerate(batch, start=1):
            summary_row = (
                db.query(EventSummary)
                .filter(EventSummary.platform_id == platform.id, EventSummary.event_id == row.id)
                .one_or_none()
            )
            location = (
                db.query(Location)
                .filter(Location.platform_id == platform.id, Location.external_key == row.slug)
                .one_or_none()
            )
            if summary_row is None or location is None:
                print(f"  [{index}/{len(batch)}] {row.slug} {row.event_date}: нет сводки или локации — пропуск")
                failed += 1
                continue

            before = db.query(RunResult).filter(RunResult.event_id == row.id).count()
            try:
                fetch_and_upsert_event_protocol(
                    db, platform, location, _summary_to_canonical(summary_row, location), summary_row
                )
                db.commit()
            except Exception as exc:  # noqa: BLE001 — одно событие не должно рушить прогон
                db.rollback()
                print(f"  [{index}/{len(batch)}] {row.slug} {row.event_date}: ОШИБКА {exc}")
                failed += 1
                continue

            after = db.query(RunResult).filter(RunResult.event_id == row.id).count()
            if after > before:
                repaired += 1
                recovered_rows += after - before
                print(f"  [{index}/{len(batch)}] {row.location_name[:26]} {row.event_date}: {before} → {after}")
            else:
                unchanged += 1
                print(f"  [{index}/{len(batch)}] {row.location_name[:26]} {row.event_date}: без изменений ({before})")

            if args.delay > 0 and index < len(batch):
                time.sleep(args.delay)

        print(
            f"\nперезалито: {repaired} (восстановлено строк: {recovered_rows}), "
            f"без изменений: {unchanged}, ошибок: {failed}"
        )
        print(f"осталось дырявых: {max(len(rows) - repaired, 0)} — запусти ещё раз для следующей пачки")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
