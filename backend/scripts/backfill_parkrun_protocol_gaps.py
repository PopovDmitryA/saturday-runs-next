#!/usr/bin/env python3
"""Заполнить пропуски мест в протоколах русского parkrun строками «неизвестного».

Протоколы собраны с профилей бегунов, и у кого профиля нет — того нет в
протоколе: 29 финишёров при номерах до 41 (Кремлёвский, 26.06.2021). Каждое
пропущенное место внутри протокола получает строку без участника и без времени
со status='unknown' (подробности — app/parkrun/protocol_gaps.py).

Только русские площадки (russian_parkrun_location_ids): от зарубежного parkrun у
нас лежат одни россияне, и «дыры» там — весь остальной забег.

Повторный прогон ничего не добавляет: ключ заглушки детерминирован, вставка —
ON CONFLICT DO NOTHING. Зато он чинит столкновения: если настоящий бегун встал на
место заглушки кодом, который ещё не умел её вытеснять (upsert_run_results до
этой правки), лишняя заглушка удаляется.

По умолчанию только показывает, что изменится. Запись — с --apply.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.session import get_session_factory
from app.parkrun.protocol_gaps import (
    PLACEHOLDER_KEY_PREFIX,
    PLACEHOLDER_STATUS,
    missing_positions,
    placeholder_key,
)
from app.services.location_catalog_service import russian_parkrun_location_ids

# Протоколы из примеров в чате — отчёт показывает их отдельно.
SAMPLES = (
    ("velikiy-novgorod-kremlevsky", date(2021, 6, 26)),
    ("velikiy-novgorod-kremlevsky", date(2021, 7, 3)),
    ("elagin-ostrov", date(2015, 6, 27)),
)

# Заглушка, на чьём месте уже стоит настоящий финишёр.
COLLIDING_SQL = """
    FROM run_results ph
    WHERE ph.participant_id IS NULL
      AND ph.external_result_key LIKE :prefix
      AND EXISTS (
          SELECT 1 FROM run_results real
          WHERE real.event_id = ph.event_id
            AND real.position = ph.position
            AND real.id <> ph.id
            AND NOT (real.participant_id IS NULL AND real.external_result_key LIKE :prefix)
      )
"""

INSERT_SQL = text(
    """
    INSERT INTO run_results (event_id, participant_id, external_result_key, position, status, fetched_at)
    VALUES (:event_id, NULL, :key, :position, :status, :now)
    ON CONFLICT (event_id, external_result_key) DO NOTHING
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Записать заглушки (без флага — только показать)")
    parser.add_argument("--top", type=int, default=25, help="Сколько локаций и протоколов показать в отчёте")
    args = parser.parse_args()

    with get_session_factory()() as db:
        location_ids = list(russian_parkrun_location_ids(db))
        rows = db.execute(
            text(
                """
                SELECT e.id, e.event_date, e.event_number, l.external_key, l.name,
                       array_agg(rr.position) AS positions
                FROM events e
                JOIN locations l ON l.id = e.location_id
                JOIN run_results rr ON rr.event_id = e.id
                WHERE e.location_id = ANY(:ids)
                GROUP BY e.id, e.event_date, e.event_number, l.external_key, l.name
                """
            ),
            {"ids": location_ids},
        ).all()

        plan: list[tuple[UUID, str, date, list[int]]] = []
        by_location: dict[str, dict[str, int]] = defaultdict(lambda: {"events": 0, "gapped": 0, "rows": 0, "holes": 0})
        worst: list[tuple[float, str, date, int | None, int, int]] = []
        total_rows = 0
        for event_id, event_date, event_number, external_key, _name, positions in rows:
            gaps = missing_positions(list(positions))
            stats = by_location[external_key]
            stats["events"] += 1
            stats["rows"] += len(positions)
            total_rows += len(positions)
            if not gaps:
                continue
            stats["gapped"] += 1
            stats["holes"] += len(gaps)
            plan.append((event_id, external_key, event_date, gaps))
            last = len(positions) + len(gaps)
            worst.append((len(gaps) / last, external_key, event_date, event_number, len(positions), last))

        holes = sum(len(gaps) for *_rest, gaps in plan)
        print(f"Русских parkrun-локаций: {len(location_ids)}, протоколов с результатами: {len(rows)}")
        print(f"Строк в протоколах: {total_rows}")
        print(f"Протоколов с пропусками: {len(plan)} ({len(plan) / max(len(rows), 1):.0%})")
        print(f"Заглушек к вставке: {holes} (+{holes / max(total_rows, 1):.1%} к строкам)")

        print("\nПримеры из чата (было финишёров → станет):")
        plan_index = {(key, event_date): gaps for _id, key, event_date, gaps in plan}
        rows_index = {(key, event_date): len(positions) for _id, event_date, _n, key, _name, positions in rows}
        for key, event_date in SAMPLES:
            gaps = plan_index.get((key, event_date), [])
            before = rows_index.get((key, event_date))
            print(f"  {key} {event_date}: {before} → {(before or 0) + len(gaps)}, места {gaps}")

        print(f"\nЛокации по числу заглушек (топ {args.top}):")
        print(f"  {'локация':38} {'протоколов':>10} {'с дырами':>9} {'строк':>7} {'заглушек':>9} {'доля':>6}")
        for key, stats in sorted(by_location.items(), key=lambda item: -item[1]["holes"])[: args.top]:
            share = stats["holes"] / max(stats["rows"] + stats["holes"], 1)
            print(
                f"  {key:38} {stats['events']:>10} {stats['gapped']:>9} {stats['rows']:>7}"
                f" {stats['holes']:>9} {share:>6.1%}"
            )

        worst.sort(reverse=True)
        print(f"\nСамые дырявые протоколы (топ {args.top}):")
        for share, key, event_date, number, known, last in worst[: args.top]:
            print(f"  {key:38} {event_date} #{number}: известно {known} из {last} ({share:.0%} заглушек)")

        prefix = {"prefix": f"{PLACEHOLDER_KEY_PREFIX}%"}
        colliding = int(db.execute(text("SELECT count(*)" + COLLIDING_SQL), prefix).scalar() or 0)
        print(f"\nЗаглушек, чьё место уже занял настоящий финишёр: {colliding}")

        if not args.apply:
            print("\nDry-run: ничего не записано. Запись — с --apply.")
            return 0

        if colliding:
            removed = db.execute(
                text("DELETE FROM run_results WHERE id IN (SELECT ph.id" + COLLIDING_SQL + ")"), prefix
            ).rowcount
            db.commit()
            print(f"Удалено столкнувшихся заглушек: {removed}")

        now = datetime.now(timezone.utc)
        for index, (event_id, key, event_date, gaps) in enumerate(plan, start=1):
            db.execute(
                INSERT_SQL,
                [
                    {
                        "event_id": event_id,
                        "key": placeholder_key(key, event_date, position),
                        "position": position,
                        "status": PLACEHOLDER_STATUS,
                        "now": now,
                    }
                    for position in gaps
                ],
            )
            if index % 500 == 0:
                db.commit()
                print(f"  … {index}/{len(plan)} протоколов")
        db.commit()
        # rowcount у executemany ненадёжен — итог считаем по базе.
        stored = db.execute(
            text("SELECT count(*) FROM run_results WHERE participant_id IS NULL AND external_result_key LIKE :prefix"),
            prefix,
        ).scalar()
        print(f"\nЗаглушек в базе: {stored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
