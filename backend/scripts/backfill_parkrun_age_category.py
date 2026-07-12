"""Backfill participants.age_category для платформы parkrun.

История: до 09.06.2026 (guard from_profile в upsert_run_results) импорт
результатов затирал возрастную группу участника age grade'ом последнего
забега («50.59%»). Испорчено ~54k строк. Правильные группы (SM25-29 и т.п.)
есть в трёх местах:

1. participants.profile_extra->>'age_category' — свежий парс страницы parkrun
   (пишется apply_parkrun_participant, есть только у перефетченных профилей);
2. легаси PG five_verst_stats.parkrun_users.actual_age_category — снапшот
   parkrun-профилей (~54k, осень 2025);
3. RunPark MSSQL api.vw_run_results.age_category — актуальная группа по
   последнему забегу RunPark (barcode A<athlete_id> совпадает с parkrun id).

Приоритет: 1 > 2 > 3. Источники 1–2 родные для parkrun («most recent age
category» со страницы профиля), RunPark — добивка для тех, кого в легаси нет.

Обновляются ТОЛЬКО существующие строки parkrun-участников, и только те, где
текущее значение NULL или не похоже на возрастную группу (процент и пр.).
Новые строки не создаются, fetched_at/profile_checked_at не трогаются.

Запуск (по умолчанию dry-run, ничего не пишет):
    docker compose exec api python scripts/backfill_parkrun_age_category.py
    docker compose exec api python scripts/backfill_parkrun_age_category.py --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy import text

sys.path.insert(0, "/app")

from app.db.session import get_session_factory  # noqa: E402
from app.parkrun.age_category import normalize_parkrun_age_group  # noqa: E402

BATCH_SIZE = 1000


def load_legacy_age_categories() -> dict[str, str]:
    """athlete_id -> возрастная группа из легаси parkrun_users."""
    from app.migration.legacy_db import legacy_connection, legacy_rows

    result: dict[str, str] = {}
    try:
        with legacy_connection() as conn:
            rows = legacy_rows(
                conn,
                """
                SELECT user_id::text AS user_id, actual_age_category
                FROM parkrun_users
                WHERE user_id IS NOT NULL AND actual_age_category IS NOT NULL
                """,
            )
    except Exception as exc:  # noqa: BLE001 — источник опциональный
        print(f"[warn] легаси-БД недоступна, источник пропущен: {exc}")
        return result
    for row in rows:
        age = normalize_parkrun_age_group(row["actual_age_category"])
        if age:
            result[str(row["user_id"]).strip()] = age
    return result


def load_runpark_age_categories() -> dict[str, str]:
    """athlete_id -> возрастная группа последнего забега RunPark (barcode A<id>)."""
    from app.runpark.mssql_client import runpark_query

    try:
        rows = runpark_query(
            """
            SELECT barcode_id, age_category
            FROM (
                SELECT barcode_id, age_category,
                       ROW_NUMBER() OVER (
                           PARTITION BY barcode_id ORDER BY event_date DESC
                       ) AS rn
                FROM api.vw_run_results
                WHERE age_category IS NOT NULL AND barcode_id IS NOT NULL
            ) t
            WHERE rn = 1
            """
        )
    except Exception as exc:  # noqa: BLE001 — источник опциональный
        print(f"[warn] RunPark MSSQL недоступен, источник пропущен: {exc}")
        return {}
    result: dict[str, str] = {}
    for row in rows:
        barcode = str(row["barcode_id"] or "").strip().upper()
        if not barcode.startswith("A"):
            continue
        age = normalize_parkrun_age_group(row["age_category"])
        if age:
            result[barcode[1:]] = age
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="Обновить не больше N строк")
    args = parser.parse_args()

    session_factory = get_session_factory()
    legacy_ages = load_legacy_age_categories()
    print(f"легаси parkrun_users: {len(legacy_ages)} валидных групп")
    runpark_ages = load_runpark_age_categories()
    print(f"RunPark vw_run_results: {len(runpark_ages)} валидных групп")

    stats: Counter[str] = Counter()
    updates: list[dict[str, str]] = []

    with session_factory() as db:
        rows = db.execute(
            text(
                """
                SELECT pa.id::text AS id,
                       pa.external_user_id,
                       pa.age_category,
                       pa.profile_extra->>'age_category' AS extra_age
                FROM participants pa
                JOIN platforms p ON p.id = pa.platform_id
                WHERE p.code = 'parkrun'
                """
            )
        ).mappings()

        for row in rows:
            stats["total"] += 1
            if normalize_parkrun_age_group(row["age_category"]):
                stats["already_valid"] += 1
                continue
            athlete_id = row["external_user_id"].strip()
            if athlete_id.startswith("unknown:"):
                stats["skipped_unknown"] += 1
                continue

            new_age = normalize_parkrun_age_group(row["extra_age"])
            source = "profile_extra"
            if new_age is None:
                new_age = legacy_ages.get(athlete_id)
                source = "legacy"
            if new_age is None:
                new_age = runpark_ages.get(athlete_id)
                source = "runpark"
            if new_age is None:
                stats["no_source"] += 1
                continue

            stats[f"from_{source}"] += 1
            updates.append({"id": row["id"], "age": new_age})
            if args.limit is not None and len(updates) >= args.limit:
                break

        print(f"\nучастников parkrun: {stats['total']}")
        print(f"уже с валидной группой: {stats['already_valid']}")
        print(f"без источника (останутся как есть): {stats['no_source']}")
        print(f"unknown-id, пропущены: {stats['skipped_unknown']}")
        print(
            f"к обновлению: {len(updates)} "
            f"(profile_extra={stats['from_profile_extra']}, "
            f"legacy={stats['from_legacy']}, runpark={stats['from_runpark']})"
        )

        if not args.apply:
            print("\nDRY-RUN: изменения не записаны. Запустите с --apply.")
            return

        written = 0
        for start in range(0, len(updates), BATCH_SIZE):
            chunk = updates[start : start + BATCH_SIZE]
            db.execute(
                text("UPDATE participants SET age_category = :age WHERE id = :id::uuid"),
                chunk,
            )
            db.commit()
            written += len(chunk)
            print(f"записано {written}/{len(updates)}")
        print("готово")


if __name__ == "__main__":
    main()
