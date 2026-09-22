"""Backfill participants.gender после миграции 053.

Источники — те же, что в gender_position_service.resolve_participant_gender:
- five_verst — первая буква age_category («М» / «Ж»);
- runpark/parkrun — вторая буква age_category («SM30-34» / «VW35-39» → M / W);
- s95 — profile_extra->platform_codes->gender («male» / «female»).

Идёт батчами со своим коммитом на каждый: один UPDATE на 323к строк держал бы
блокировку на participants минуты, и сайт бы встал. Батч трогает несколько тысяч
строк и отпускает — читатели не замечают. Скрипт идемпотентен: строки с уже
проставленным полом пропускаются, так что можно догонять повторным запуском.

Строки берутся ORDER BY id + FOR UPDATE SKIP LOCKED. Без этого батч и апсерт
синка блокируют строки в разном порядке и ловят deadlock (поймано локально на
первом же прогоне). SKIP LOCKED пропускает строки, которые прямо сейчас пишет
воркер, — их подберёт следующий проход, поэтому в конце печатается покрытие:
если оно не 100% от доступного, достаточно запустить скрипт ещё раз.

Запуск:
    docker compose exec api python scripts/backfill_participant_gender.py
    docker compose exec api python scripts/backfill_participant_gender.py --batch-size 20000
"""

from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import text

sys.path.insert(0, "/app")

from app.db.session import get_session_factory  # noqa: E402

STEPS: list[tuple[str, str]] = [
    (
        "five_verst",
        """
        UPDATE participants p
        SET gender = CASE substr(p.age_category, 1, 1)
                         WHEN 'М' THEN 'male'
                         WHEN 'Ж' THEN 'female'
                     END
        WHERE p.id IN (
            SELECT p2.id
            FROM participants p2
            JOIN platforms pl ON pl.id = p2.platform_id
            WHERE pl.code = 'five_verst'
              AND p2.gender IS NULL
              AND substr(p2.age_category, 1, 1) IN ('М', 'Ж')
            ORDER BY p2.id
            LIMIT :batch_size
            FOR UPDATE OF p2 SKIP LOCKED
        )
        """,
    ),
    (
        "runpark+parkrun",
        """
        UPDATE participants p
        SET gender = CASE substr(p.age_category, 2, 1)
                         WHEN 'M' THEN 'male'
                         WHEN 'W' THEN 'female'
                     END
        WHERE p.id IN (
            SELECT p2.id
            FROM participants p2
            JOIN platforms pl ON pl.id = p2.platform_id
            WHERE pl.code IN ('runpark', 'parkrun')
              AND p2.gender IS NULL
              AND length(p2.age_category) >= 2
              AND substr(p2.age_category, 2, 1) IN ('M', 'W')
            ORDER BY p2.id
            LIMIT :batch_size
            FOR UPDATE OF p2 SKIP LOCKED
        )
        """,
    ),
    (
        "s95",
        """
        UPDATE participants p
        SET gender = p.profile_extra #>> '{platform_codes,gender}'
        WHERE p.id IN (
            SELECT p2.id
            FROM participants p2
            JOIN platforms pl ON pl.id = p2.platform_id
            WHERE pl.code = 's95'
              AND p2.gender IS NULL
              AND p2.profile_extra #>> '{platform_codes,gender}' IN ('male', 'female')
            ORDER BY p2.id
            LIMIT :batch_size
            FOR UPDATE OF p2 SKIP LOCKED
        )
        """,
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="строк за один коммит (по умолчанию 10000)",
    )
    args = parser.parse_args()

    db = get_session_factory()()
    total = 0
    try:
        for name, sql in STEPS:
            started = time.monotonic()
            updated = 0
            while True:
                result = db.execute(text(sql), {"batch_size": args.batch_size})
                db.commit()
                if not result.rowcount:
                    break
                updated += result.rowcount
                print(f"  {name}: +{result.rowcount} (всего {updated})", flush=True)
            total += updated
            print(f"{name}: {updated} строк за {time.monotonic() - started:.1f}s", flush=True)

        stats = db.execute(
            text(
                """
                SELECT pl.code,
                       count(*) AS total,
                       count(p.gender) AS with_gender
                FROM participants p
                JOIN platforms pl ON pl.id = p.platform_id
                GROUP BY pl.code
                ORDER BY count(*) DESC
                """
            )
        ).all()
        print(f"\nИтого обновлено: {total}")
        print("Покрытие после бэкфилла:")
        for code, count_all, with_gender in stats:
            pct = 100.0 * with_gender / count_all if count_all else 0.0
            print(f"  {code:<12} {with_gender}/{count_all} ({pct:.1f}%)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
